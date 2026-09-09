#!/usr/bin/env python3
"""AI worker — deteksi kejadian dari CCTV, kirim alert ke dashboard.

Jalankan terpisah dari dashboard (boleh di mesin lain yang punya GPU):

    python ai/worker.py --config ai/zones.json

Yang dideteksi:
  1. Operator tidak di area   — tidak ada orang di zona line > absent_seconds
  2. Kerumunan di line        — >= crowd_min orang di zona > crowd_seconds
  3. Mesin stop tanpa penanganan — lampu tower merah menyala > response_seconds
                                   dan tidak ada orang di zona

Alert dikirim ke dashboard lewat POST /api/alerts.
"""
import argparse
import json
import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import requests

from lamp import read_all as read_lamps

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from ultralytics import YOLO
except ImportError:
    sys.exit("ultralytics belum terpasang. Jalankan: pip install -r ai/requirements.txt")

log = logging.getLogger("ai")

PERSON_CLASS = 0          # class 'person' pada model COCO


# ---------------------------------------------------------------- util
def poly_from_percent(points, w, h):
    """Ubah polygon persen -> piksel."""
    return np.array([[int(x / 100 * w), int(y / 100 * h)] for x, y in points], np.int32)


def rect_from_percent(rect, w, h):
    x, y, rw, rh = rect
    return (int(x / 100 * w), int(y / 100 * h), int(rw / 100 * w), int(rh / 100 * h))


def box_center_bottom(box):
    """Titik acuan orang = tengah-bawah bounding box (posisi kaki di lantai)."""
    x1, y1, x2, y2 = box
    return (int((x1 + x2) / 2), int(y2))


# ---------------------------------------------------------------- worker
class CameraWorker(threading.Thread):
    """Satu thread per kamera."""

    def __init__(self, cam_cfg, defaults, dashboard_url, model):
        super().__init__(daemon=True, name=cam_cfg["line_id"])
        self.cfg = {**defaults, **cam_cfg}
        self.line_id = cam_cfg["line_id"]
        self.rtsp = cam_cfg["rtsp"]
        self.dashboard = dashboard_url.rstrip("/")
        self.model = model
        self.stop_flag = threading.Event()

        # state penghitung waktu
        self.last_seen_person = time.time()
        self.crowd_since = None
        self.red_since = None
        self.last_alert = {}          # label -> waktu terakhir dikirim
        self.lamps = self.cfg.get("machines", [])   # ROI lampu per mesin
        self.zone = self.cfg.get("zone", [])
        self.last_status_push = 0.0
        self.last_cal_fetch = 0.0
        self.cal_version = None

    # ---------- kalibrasi dari dashboard ----------
    def refresh_calibration(self):
        """Ambil zona & ROI lampu terbaru dari dashboard.

        Operator mengkalibrasi lewat halaman detail kamera di dashboard;
        worker menariknya di sini, jadi tidak perlu menyalin file ke
        server AI atau me-restart worker.
        """
        if not self.cfg.get("use_dashboard_calibration", True):
            return
        now = time.time()
        if now - self.last_cal_fetch < self.cfg.get("calibration_interval", 30):
            return
        self.last_cal_fetch = now
        try:
            r = requests.get(
                f"{self.dashboard}/api/lines/{self.line_id}/calibration",
                timeout=5)
            if r.status_code == 404:
                return                      # belum dikalibrasi, pakai zones.json
            r.raise_for_status()
            cal = r.json()
        except (requests.RequestException, ValueError):
            return                          # dashboard mati: pakai yang terakhir

        version = json.dumps(cal, sort_keys=True)
        if version == self.cal_version:
            return
        self.cal_version = version

        zone = cal.get("zone") or []
        lamps = cal.get("machines") or []
        if len(zone) >= 3:
            self.zone = [tuple(p) for p in zone]
            self.zone_px = None             # paksa hitung ulang ke piksel
        self.lamps = lamps
        log.info("[%s] kalibrasi diperbarui dari dashboard: "
                 "%d titik zona, %d ROI lampu",
                 self.line_id, len(zone), len(lamps))

    # ---------- pengiriman alert ----------
    def send_alert(self, label, activity, duration, confidence, severity="high",
                   object_type="Person"):
        cooldown = self.cfg["cooldown_seconds"]
        now = time.time()
        if now - self.last_alert.get(label, 0) < cooldown:
            return                      # jangan spam alert yang sama
        self.last_alert[label] = now

        payload = {
            "line_id": self.line_id,
            "label": label,
            "activity": activity,
            "confidence": int(confidence * 100) if confidence <= 1 else int(confidence),
            "object_type": object_type,
            "duration": int(duration),
            "severity": severity,
            "detected_at": datetime.now().strftime("%H:%M:%S"),
        }
        try:
            r = requests.post(f"{self.dashboard}/api/alerts", json=payload, timeout=5)
            if r.ok:
                log.info("[%s] ALERT terkirim: %s (%ds)", self.line_id, label, duration)
            else:
                log.warning("[%s] dashboard menolak: %s %s",
                            self.line_id, r.status_code, r.text[:120])
        except requests.RequestException as e:
            log.warning("[%s] gagal kirim alert: %s", self.line_id, e)

    def push_machine_status(self, results):
        """Kirim status 10 mesin (hasil baca lampu) ke dashboard."""
        now = time.time()
        if now - self.last_status_push < self.cfg.get("status_interval", 10):
            return
        self.last_status_push = now
        body = {"machines": [{"no": r["no"], "status": r["status"],
                              "color": r["color"]} for r in results]}
        try:
            requests.post(f"{self.dashboard}/api/lines/{self.line_id}/machines",
                          json=body, timeout=5)
        except requests.RequestException as e:
            log.warning("[%s] gagal kirim status mesin: %s", self.line_id, e)

    # ---------- aturan deteksi ----------
    def evaluate(self, n_in_zone, n_stop, now):
        c = self.cfg

        if n_in_zone > 0:
            self.last_seen_person = now

        # 1. operator meninggalkan line
        absent = now - self.last_seen_person
        if absent > c["absent_seconds"]:
            self.send_alert(
                "Operator tidak di area",
                "Tidak ada orang terdeteksi di zona line",
                absent, 0.9,
            )

        # 2. kerumunan
        if n_in_zone >= c["crowd_min"]:
            self.crowd_since = self.crowd_since or now
            if now - self.crowd_since > c["crowd_seconds"]:
                self.send_alert(
                    "Kerumunan di line",
                    f"{n_in_zone} orang berkumpul di zona line",
                    now - self.crowd_since, 0.85, severity="med",
                )
        else:
            self.crowd_since = None

        # 3. mesin stop tanpa penanganan
        if n_stop > 0:
            self.red_since = self.red_since or now
            waited = now - self.red_since
            if waited > c["response_seconds"] and n_in_zone == 0:
                self.send_alert(
                    "Mesin stop tanpa penanganan",
                    f"{n_stop} mesin lampu merah, tidak ada operator di area",
                    waited, 0.95, object_type="Machine",
                )
        else:
            self.red_since = None

    # ---------- loop utama ----------
    def run(self):
        interval = 1.0 / max(1, self.cfg["fps"])
        while not self.stop_flag.is_set():
            cap = cv2.VideoCapture(self.rtsp, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not cap.isOpened():
                log.warning("[%s] tidak bisa membuka stream, coba lagi 10 detik",
                            self.line_id)
                time.sleep(10)
                continue

            log.info("[%s] stream terhubung", self.line_id)
            self.zone_px = None
            last_run = 0.0

            while not self.stop_flag.is_set():
                ok = cap.grab()                 # buang frame, jangan decode semua
                if not ok:
                    log.warning("[%s] stream terputus", self.line_id)
                    break

                now = time.time()
                if now - last_run < interval:
                    continue                    # sampling: hanya proses N fps
                last_run = now

                ok, frame = cap.retrieve()
                if not ok or frame is None:
                    continue

                self.refresh_calibration()

                h, w = frame.shape[:2]
                if self.zone_px is None:
                    self.zone_px = poly_from_percent(self.zone, w, h)

                # --- deteksi orang ---
                res = self.model.predict(
                    frame, classes=[PERSON_CLASS], verbose=False,
                    conf=self.cfg["min_confidence"],
                )[0]

                n_in_zone = 0
                for box in res.boxes.xyxy.cpu().numpy():
                    px, py = box_center_bottom(box)
                    if cv2.pointPolygonTest(self.zone_px, (px, py), False) >= 0:
                        n_in_zone += 1

                # --- baca lampu tower tiap mesin ---
                n_stop = 0
                if self.lamps:
                    results, counts = read_lamps(frame, self.lamps)
                    n_stop = counts["stop"]
                    self.push_machine_status(results)

                self.evaluate(n_in_zone, n_stop, now)

            cap.release()
            time.sleep(3)

    def stop(self):
        self.stop_flag.set()


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="AI worker CCTV -> dashboard")
    ap.add_argument("--config", default="ai/zones.json", help="file konfigurasi JSON")
    ap.add_argument("--model", default="yolov8n.pt",
                    help="model YOLO (yolov8n=ringan, yolov8s/m=lebih akurat)")
    ap.add_argument("--no-dashboard-calibration", action="store_true",
                    help="abaikan kalibrasi dari dashboard, pakai zones.json saja")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
    )

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        sys.exit(f"Config tidak ditemukan: {cfg_path}\n"
                 f"Salin dari ai/zones.example.json lalu sesuaikan.")
    cfg = json.loads(cfg_path.read_text())

    cameras = cfg.get("cameras", [])
    if not cameras:
        sys.exit("Tidak ada kamera di config.")

    defaults = cfg.get("defaults", {})
    if args.no_dashboard_calibration:
        defaults["use_dashboard_calibration"] = False
    else:
        log.info("Kalibrasi diambil dari dashboard tiap %ds "
                 "(zones.json dipakai sebagai cadangan)",
                 defaults.get("calibration_interval", 30))

    log.info("Memuat model %s ...", args.model)
    model = YOLO(args.model)          # 1 model dipakai bersama semua thread

    workers = [
        CameraWorker(cam, defaults, cfg["dashboard_url"], model)
        for cam in cameras
    ]
    for w in workers:
        w.start()
    log.info("%d kamera dipantau. Ctrl+C untuk berhenti.", len(workers))

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Menghentikan worker...")
        for w in workers:
            w.stop()
        for w in workers:
            w.join(timeout=5)


if __name__ == "__main__":
    main()
