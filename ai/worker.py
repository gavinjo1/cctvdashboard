#!/usr/bin/env python3
"""AI worker — deteksi kejadian dari CCTV, kirim alert ke dashboard.

Jalankan terpisah dari dashboard (boleh di mesin lain yang punya GPU):

    python ai/worker.py --config ai/zones.json

Yang dideteksi:
  1. Operator tidak di area   — tidak ada orang di zona line > absent_seconds
  2. Kerumunan di line        — >= crowd_min orang di zona > crowd_seconds
  3. Mesin stop tanpa penanganan — indikator masalah (putus pakan / putus
                                   lusi) menyala > response_seconds dan tidak
                                   ada orang di zona

Menara lampu dibaca berdasarkan POSISI segmen, bukan warna; "semua padam"
berarti mesin jalan normal. Lihat ai/lamp.py.

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

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lamp import buat_pembaca, read_tower

try:
    from ultralytics import YOLO
except ImportError:
    sys.exit("ultralytics belum terpasang. Jalankan: pip install -r ai/requirements.txt")

log = logging.getLogger("ai")

PERSON_CLASS = 0          # class 'person' pada model COCO

#: Urutan indikator menara, ATAS -> BAWAH. Artinya datang dari POSISI,
#: bukan warna. Ditimpa per line lewat "indikator" di zones.json.
#:
#: Bawaan ini mengikuti dokumentasi air-jet loom umum (biru, merah, kuning,
#: hijau dari atas). TODO(pabrik): PASTIKAN ke bagian perawatan — menara
#: andon sering dikonfigurasi ulang per pabrik, dan salah memetakan kuning
#: vs merah membuat putus pakan tercatat sebagai kerusakan mesin.
INDIKATOR_BAWAAN = ["loose_weft", "mesin_stop", "putus_pakan", "jalan"]

#: Status mesin saat TIDAK ADA segmen menyala. Lihat catatan di ai/lamp.py —
#: ini harus dipastikan dengan melihat mesin yang sedang berproduksi.
#: TODO(pabrik): "run" bila menara padam saat jalan, "off" bila hijau
#: seharusnya menyala terus.
SAAT_GELAP_BAWAAN = "run"

#: jeda sebelum menyambung ulang stream yang putus (detik)
RECONNECT_DELAY = 3
OPEN_RETRY_DELAY = 10

#: batas waktu buka & baca stream (milidetik). Tanpa ini OpenCV bisa
#: menggantung selamanya pada kamera yang tidak menjawab: thread tetap
#: terlihat "hidup" padahal tidak pernah memproses frame lagi.
OPEN_TIMEOUT_MS = 10000
READ_TIMEOUT_MS = 10000

#: kegagalan memproses frame berturut-turut sebelum stream disambung ulang
ERROR_BURST = 10

#: kamera dianggap macet bila sekian detik tidak menghasilkan frame
STALE_AFTER = 60

#: jeda pemeriksaan kesehatan semua kamera oleh supervisor di main()
SUPERVISE_INTERVAL = 15


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
        # TODO(pabrik): api_key WAJIB diisi di zones.json, sama dengan
        # CCTV_API_KEY di dashboard. Tanpa ini endpoint tulis menolak.
        self.headers = {}
        key = self.cfg.get("api_key") or ""
        if key:
            self.headers["X-API-Key"] = key
        self.model = model
        self.stop_flag = threading.Event()

        # state penghitung waktu
        self.last_seen_person = time.time()
        self.crowd_since = None
        self.red_since = None
        self.last_alert = {}          # label -> waktu terakhir dikirim
        self.lamps = self.cfg.get("machines", [])   # kotak menara per mesin
        self.indikator = self.cfg.get("indikator", INDIKATOR_BAWAAN)
        # PembacaMenara menyimpan riwayat kedip, jadi HARUS dipakai ulang
        # antar frame. Dibangun ulang hanya kalau kalibrasi berubah.
        self.pembaca = buat_pembaca(self.lamps, self.indikator,
                                    self.cfg.get("ambang_lampu"))
        self.baseline_siap = False
        self.zone = self.cfg.get("zone", [])
        self.zone_px = None
        self._warned_zone = False
        self.last_status_push = 0.0
        self.last_cal_fetch = 0.0
        self.cal_version = None

        # kesehatan — dibaca supervisor di main(). Tanpa angka-angka ini
        # tidak ada cara membedakan kamera yang tenang karena pabrik sepi
        # dari kamera yang sudah berhenti bekerja.
        self.started_at = time.time()
        self.last_frame_at = 0.0
        self.frames = 0
        self.errors = 0

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

        # Kalibrasi zona-saja dari dashboard TIDAK boleh menghapus ROI lampu
        # yang datang dari zones.json. Tanpa penjagaan ini, menyimpan zona
        # tanpa menggambar kotak lampu membuat line ini berhenti melaporkan
        # status mesin — diam-diam, tanpa pesan kesalahan di mana pun.
        if lamps:
            self.lamps = lamps
            self.pembaca = buat_pembaca(self.lamps, self.indikator,
                                        self.cfg.get("ambang_lampu"))
            self.baseline_siap = False
        elif self.lamps:
            log.warning("[%s] kalibrasi dashboard tidak berisi ROI lampu — "
                        "%d ROI dari zones.json tetap dipakai. Untuk benar-benar "
                        "mengosongkannya, hapus juga dari zones.json.",
                        self.line_id, len(self.lamps))

        log.info("[%s] kalibrasi diperbarui dari dashboard: "
                 "%d titik zona, %d ROI lampu",
                 self.line_id, len(zone), len(self.lamps))

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
            r = requests.post(f"{self.dashboard}/api/alerts", json=payload,
                              headers=self.headers, timeout=5)
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
                              "indikator": r["indikator"],
                              "kedip": r["kedip"]} for r in results]}
        try:
            requests.post(f"{self.dashboard}/api/lines/{self.line_id}/machines",
                          json=body, headers=self.headers, timeout=5)
        except requests.RequestException as e:
            log.warning("[%s] gagal kirim status mesin: %s", self.line_id, e)

    # ---------- aturan deteksi ----------
    def evaluate(self, n_in_zone, n_stop, now, zone_ok=True):
        c = self.cfg

        # Tanpa zona terkalibrasi, jumlah orang SELALU nol. Menjalankan
        # aturan berbasis orang dalam keadaan itu akan membanjiri dashboard
        # dengan "Operator tidak di area" palsu dari setiap kamera yang
        # belum dikalibrasi — dan alert palsu yang banyak membuat operator
        # berhenti mempercayai semua alert.
        if not zone_ok:
            self.last_seen_person = now     # jangan menumpuk waktu absen
            self.crowd_since = None
            self.red_since = None
            return

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
                    f"{n_stop} mesin memberi indikator masalah, "
                    f"tidak ada operator di area",
                    waited, 0.95, object_type="Machine",
                )
        else:
            self.red_since = None

    # ---------- zona ----------
    def zone_polygon(self, w, h):
        """Polygon zona dalam piksel, atau None kalau belum dikalibrasi.

        Zona kosong atau kurang dari 3 titik bukan polygon, dan
        cv2.pointPolygonTest melempar kesalahan bila diberi bentuk seperti
        itu. Kesalahan itu hanya terjadi saat ADA orang terdeteksi — jadi
        kamera yang belum dikalibrasi lolos pengujian di ruangan kosong,
        lalu mati pada shift pertama saat seseorang lewat.

        Lebih baik melewati deteksi orang dan tetap membaca lampu tower:
        kamera yang belum dikalibrasi masih berguna untuk status mesin.
        """
        if len(self.zone) < 3:
            if not self._warned_zone:
                self._warned_zone = True
                log.warning("[%s] zona belum dikalibrasi (%d titik) — deteksi "
                            "orang DIMATIKAN untuk line ini, pembacaan lampu "
                            "tetap jalan. Kalibrasi lewat dashboard.",
                            self.line_id, len(self.zone))
            return None
        self._warned_zone = False
        return poly_from_percent(self.zone, w, h)

    # ---------- pemrosesan satu frame ----------
    def process_frame(self, frame, now):
        """Deteksi orang, baca lampu tower, lalu jalankan aturan alert."""
        self.refresh_calibration()

        h, w = frame.shape[:2]
        if self.zone_px is None:
            self.zone_px = self.zone_polygon(w, h)

        # --- deteksi orang (hanya bila zona sudah dikalibrasi) ---
        n_in_zone = 0
        zone_ok = self.zone_px is not None
        if zone_ok:
            res = self.model.predict(
                frame, classes=[PERSON_CLASS], verbose=False,
                conf=self.cfg["min_confidence"],
            )[0]
            for box in res.boxes.xyxy.cpu().numpy():
                px, py = box_center_bottom(box)
                if cv2.pointPolygonTest(self.zone_px, (px, py), False) >= 0:
                    n_in_zone += 1

        # --- baca lampu tower tiap mesin ---
        n_stop = 0
        if self.pembaca:
            # Baseline = kecerahan saat menara padam. Di pabrik ini keadaan
            # normal memang gelap (semua padam = mesin jalan), jadi frame
            # pertama yang seluruh menaranya gelap sudah cukup.
            if not self.baseline_siap:
                for p in self.pembaca:
                    p.rekam_baseline(frame)
                self.baseline_siap = True
                log.info("[%s] baseline lampu direkam dari %d menara",
                         self.line_id, len(self.pembaca))

            results, counts = read_tower(
                frame, self.pembaca, now,
                peran=self.cfg.get("peran_indikator"),
                saat_gelap=self.cfg.get("saat_gelap", SAAT_GELAP_BAWAAN))
            n_stop = counts["stop"]
            self.push_machine_status(results)

        self.evaluate(n_in_zone, n_stop, now, zone_ok=zone_ok)

    # ---------- satu sesi koneksi ----------
    def session(self):
        """Tarik stream sampai putus. Kembali = perlu disambung ulang."""
        cap = cv2.VideoCapture(self.rtsp, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        # Tanpa batas waktu, kamera yang tidak menjawab membuat grab()
        # menggantung selamanya: thread tetap hidup tetapi berhenti bekerja,
        # dan tidak ada yang bisa membedakannya dari kamera yang sehat.
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_TIMEOUT_MS)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, READ_TIMEOUT_MS)

        if not cap.isOpened():
            cap.release()
            log.warning("[%s] tidak bisa membuka stream, coba lagi %d detik",
                        self.line_id, OPEN_RETRY_DELAY)
            self.stop_flag.wait(OPEN_RETRY_DELAY)
            return

        log.info("[%s] stream terhubung", self.line_id)
        self.zone_px = None
        interval = 1.0 / max(1, self.cfg["fps"])
        last_run = 0.0
        beruntun = 0                        # kegagalan frame berturut-turut

        try:
            while not self.stop_flag.is_set():
                if not cap.grab():          # buang frame, jangan decode semua
                    log.warning("[%s] stream terputus", self.line_id)
                    return

                now = time.time()
                if now - last_run < interval:
                    continue                # sampling: hanya proses N fps
                last_run = now

                ok, frame = cap.retrieve()
                if not ok or frame is None:
                    continue

                try:
                    self.process_frame(frame, now)
                except Exception:
                    self.errors += 1
                    beruntun += 1
                    # Satu frame gagal bukan alasan memutus koneksi; kegagalan
                    # beruntun berarti ada yang salah secara tetap.
                    log.exception("[%s] gagal memproses frame "
                                  "(%d berturut-turut)", self.line_id, beruntun)
                    if beruntun >= ERROR_BURST:
                        log.error("[%s] %d kegagalan berturut-turut — "
                                  "menyambung ulang stream",
                                  self.line_id, beruntun)
                        return
                else:
                    beruntun = 0
                    self.frames += 1
                    self.last_frame_at = now
        finally:
            cap.release()

    # ---------- loop utama ----------
    def run(self):
        """Ulangi sesi sampai diminta berhenti.

        Seluruh isi dibungkus try/except. Tanpa ini satu kesalahan tak
        terduga — frame rusak, model gagal, zona tidak valid — mengakhiri
        thread ini SELAMANYA: proses induk tetap hidup, log tidak berkata
        apa-apa, dan dashboard terus menampilkan keadaan terakhir line ini
        seolah kameranya masih bekerja. Kamera yang buta diam-diam adalah
        kegagalan paling berbahaya di sistem ini.
        """
        while not self.stop_flag.is_set():
            try:
                self.session()
            except Exception:
                self.errors += 1
                log.exception("[%s] sesi berakhir karena kesalahan — "
                              "menyambung ulang", self.line_id)
            if not self.stop_flag.is_set():
                self.stop_flag.wait(RECONNECT_DELAY)
        log.info("[%s] worker berhenti", self.line_id)

    # ---------- kesehatan ----------
    def health(self):
        """Ringkasan untuk supervisor di main()."""
        acuan = self.last_frame_at or self.started_at
        return {
            "line_id": self.line_id,
            "alive": self.is_alive(),
            "frames": self.frames,
            "errors": self.errors,
            "idle_sec": time.time() - acuan,
            "pernah_jalan": bool(self.last_frame_at),
        }

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

    api_key = cfg.get("api_key", "")
    if not api_key:
        log.warning("api_key kosong di config — dashboard akan menolak "
                    "kiriman alert & status mesin kalau CCTV_API_KEY diisi.")
    for cam in cameras:
        cam.setdefault("api_key", api_key)

    workers = [
        CameraWorker(cam, defaults, cfg["dashboard_url"], model)
        for cam in cameras
    ]
    for w in workers:
        w.start()
    log.info("%d kamera dipantau. Ctrl+C untuk berhenti.", len(workers))

    # Supervisor. Kamera yang mati atau macet HARUS terlihat: sebelum ini,
    # thread yang berhenti meninggalkan proses tetap hidup tanpa sepatah kata
    # pun di log, dan line itu berhenti terpantau tanpa ada yang tahu.
    stale_after = defaults.get("stale_after_seconds", STALE_AFTER)
    macet = set()
    try:
        while True:
            time.sleep(SUPERVISE_INTERVAL)
            for i, w in enumerate(workers):
                h = w.health()
                line_id = h["line_id"]

                if not h["alive"]:
                    log.error("[%s] thread berhenti setelah %d frame — "
                              "dijalankan ulang", line_id, h["frames"])
                    baru = CameraWorker(cameras[i], defaults,
                                        cfg["dashboard_url"], model)
                    baru.start()
                    workers[i] = baru
                    macet.discard(line_id)
                elif h["idle_sec"] > stale_after:
                    if line_id not in macet:
                        macet.add(line_id)
                        log.error("[%s] tidak ada frame selama %d detik (%s) — "
                                  "line ini TIDAK terpantau",
                                  line_id, int(h["idle_sec"]),
                                  "belum pernah berjalan" if not h["pernah_jalan"]
                                  else "stream macet atau kamera mati")
                elif line_id in macet:
                    macet.discard(line_id)
                    log.info("[%s] kembali menghasilkan frame", line_id)
    except KeyboardInterrupt:
        log.info("Menghentikan worker...")
        for w in workers:
            w.stop()
        for w in workers:
            w.join(timeout=5)


if __name__ == "__main__":
    main()
