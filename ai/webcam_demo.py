#!/usr/bin/env python3
"""Webcam sebagai kamera uji — pengganti CCTV + go2rtc saat belum ada kamera.

Satu proses melakukan tiga hal sekaligus (supaya webcam tidak direbut
dua proses, karena macOS hanya mengizinkan satu):

  1. Ambil frame dari webcam
  2. Deteksi orang + gambar overlay (zona & kotak deteksi)
  3. Layani MJPEG ke dashboard  +  kirim alert ke dashboard

Jalankan:
    python ai/webcam_demo.py

Lalu jalankan dashboard dengan:
    CCTV_STREAM_MODE=img \\
    CCTV_STREAM_URL="http://127.0.0.1:1984/frame?src={id}" \\
    python run.py

CATATAN: ini alat UJI COBA. Untuk produksi tetap pakai go2rtc + ai/worker.py;
webcam_demo.py hanya menirukan keduanya dengan satu webcam.
"""
import argparse
import json
import logging
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import cv2
import numpy as np
import requests

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lamp import draw_lamps, buat_pembaca, read_tower

log = logging.getLogger("webcam")

#: urut ATAS -> BAWAH, sama dengan ai/lamp.py
INDIKATOR_DEMO = ["mesin_stop", "putus_pakan", "putus_lusi", "setup"]

# ---------------------------------------------------------------- state
class Shared:
    """Frame terakhir + hasil deteksi, dipakai bersama HTTP handler."""
    def __init__(self):
        self.lock = threading.Lock()
        self.jpeg = None            # frame terakhir (sudah di-encode)
        self.people = 0             # jumlah orang di zona
        self.lamps = []             # hasil baca lampu tiap mesin
        self.counts = {"run": 0, "idle": 0, "stop": 0, "off": 0}
        self.detector = "-"
        self.fps = 0.0


shared = Shared()


# ---------------------------------------------------------------- detektor
class HogDetector:
    """Detektor bawaan OpenCV — tidak perlu unduh model. Akurasi sedang."""
    name = "HOG (OpenCV)"

    def __init__(self):
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def detect(self, frame):
        small = cv2.resize(frame, (640, int(640 * frame.shape[0] / frame.shape[1])))
        scale = frame.shape[1] / small.shape[1]
        rects, weights = self.hog.detectMultiScale(
            small, winStride=(8, 8), padding=(8, 8), scale=1.05)
        out = []
        for (x, y, w, h), wt in zip(rects, weights):
            if wt < 0.4:
                continue
            out.append((int(x * scale), int(y * scale),
                        int((x + w) * scale), int((y + h) * scale), float(wt)))
        return out


class YoloDetector:
    """YOLO — sama dengan yang dipakai worker produksi."""
    name = "YOLOv8n"

    def __init__(self, model="yolov8n.pt", conf=0.45):
        from ultralytics import YOLO
        self.model = YOLO(model)
        self.conf = conf

    def detect(self, frame):
        res = self.model.predict(frame, classes=[0], conf=self.conf, verbose=False)[0]
        out = []
        for box, c in zip(res.boxes.xyxy.cpu().numpy(),
                          res.boxes.conf.cpu().numpy()):
            x1, y1, x2, y2 = box
            out.append((int(x1), int(y1), int(x2), int(y2), float(c)))
        return out


def build_detector(kind):
    if kind == "hog":
        return HogDetector()
    try:
        return YoloDetector()
    except Exception as e:
        log.warning("YOLO tidak tersedia (%s) — pakai HOG bawaan OpenCV", e)
        return HogDetector()


# ---------------------------------------------------------------- capture
class Capture(threading.Thread):
    def __init__(self, args, cap):
        super().__init__(daemon=True)
        self.a = args
        self.cap = cap
        self.stop_flag = threading.Event()
        self.det = build_detector(args.detector)
        shared.detector = self.det.name

        # penghitung waktu untuk aturan alert
        self.last_seen = time.time()
        self.crowd_since = None
        self.last_alert = {}
        self.last_cal_fetch = 0.0
        self.cal_version = None
        self._pembaca = None

    def _headers(self):
        return {"X-API-Key": self.a.api_key} if self.a.api_key else {}

    # ---------- kalibrasi dari dashboard ----------
    def refresh_calibration(self):
        """Ambil zona & ROI lampu dari dashboard.

        Supaya yang digambar di dashboard dan yang dipakai di sini SATU
        sumber. Tanpa ini, overlay di gambar memakai --zone/--lamp dari
        CLI sementara dashboard menyimpan yang lain — dua-duanya tampil
        dan membingungkan.
        """
        if not self.a.dashboard_calibration:
            return
        now = time.time()
        if now - self.last_cal_fetch < 10:
            return
        self.last_cal_fetch = now
        try:
            r = requests.get(
                self.a.dashboard.rstrip("/") +
                "/api/lines/%s/calibration" % self.a.line_id, timeout=4)
            if r.status_code == 404:
                return                       # belum dikalibrasi: pakai CLI
            r.raise_for_status()
            cal = r.json()
        except (requests.RequestException, ValueError):
            return

        version = json.dumps(cal, sort_keys=True)
        if version == self.cal_version:
            return
        self.cal_version = version

        zone = cal.get("zone") or []
        lamps = cal.get("machines") or []
        if len(zone) >= 3:
            self.a.zone = [tuple(p) for p in zone]
        self.a.lamps = lamps
        self._pembaca = None             # kotak berubah -> bangun ulang pembaca
        with shared.lock:
            shared.lamps = []                # hasil lama tidak berlaku lagi
            shared.counts = {"run": 0, "idle": 0, "stop": 0, "off": 0}
        log.info("Kalibrasi diperbarui dari dashboard: %d titik zona, "
                 "%d ROI lampu", len(zone), len(lamps))

    # ---------- kirim alert ----------
    def send_alert(self, label, activity, duration, conf, severity="high"):
        if time.time() - self.last_alert.get(label, 0) < self.a.cooldown:
            return
        self.last_alert[label] = time.time()
        payload = {
            "line_id": self.a.line_id,
            "label": label,
            "activity": activity,
            "confidence": int(conf * 100),
            "object_type": "Person",
            "duration": int(duration),
            "severity": severity,
            "detected_at": datetime.now().strftime("%H:%M:%S"),
        }
        try:
            r = requests.post(self.a.dashboard.rstrip("/") + "/api/alerts",
                              json=payload, headers=self._headers(), timeout=5)
            log.info("ALERT -> %s : %s (%ds)  [%s]",
                     self.a.line_id, label, duration, r.status_code)
        except requests.RequestException as e:
            log.warning("gagal kirim alert: %s", e)

    def push_machine_status(self, results):
        """Kirim status mesin ke dasbor.

        Interval ini menentukan ketepatan waktu pada catatan episode:
        perubahan warna baru tercatat pada kiriman berikutnya. Untuk
        pengumpulan data uji, turunkan dengan --status-interval 1.
        """
        now = time.time()
        if now - getattr(self, "_last_push", 0) < self.a.status_interval:
            return
        self._last_push = now
        body = {"machines": [{"no": r["no"], "status": r["status"],
                              "indikator": r["indikator"],
                              "kedip": r["kedip"]} for r in results]}
        try:
            requests.post(self.a.dashboard.rstrip("/") +
                          "/api/lines/%s/machines" % self.a.line_id,
                          json=body, headers=self._headers(), timeout=5)
        except requests.RequestException as e:
            log.warning("gagal kirim status mesin: %s", e)

    # ---------- aturan ----------
    def evaluate(self, n, now):
        if n > 0:
            self.last_seen = now
        absent = now - self.last_seen
        if absent > self.a.absent:
            self.send_alert("Operator tidak di area",
                            "Tidak ada orang terdeteksi di zona line", absent, 0.9)
        if n >= self.a.crowd_min:
            self.crowd_since = self.crowd_since or now
            if now - self.crowd_since > self.a.crowd_seconds:
                self.send_alert("Kerumunan di line",
                                f"{n} orang berkumpul di zona line",
                                now - self.crowd_since, 0.85, "med")
        else:
            self.crowd_since = None

    # ---------- loop ----------
    def run(self):
        cap = self.cap

        # Warm-up: webcam Mac sering mengirim beberapa frame hitam di awal
        # sambil menyesuaikan exposure. Buang dulu.
        for _ in range(10):
            cap.read()

        log.info("Sumber terbuka — detektor: %s", self.det.name)
        self.black_warned = False

        interval = 1.0 / max(1, self.a.fps)
        last, boxes, t_fps = 0.0, [], time.time()
        frames = 0

        while not self.stop_flag.is_set():
            ok, frame = cap.read()
            if not ok:
                if self.a.loop and self.a.source:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)   # ulang dari awal
                    continue
                time.sleep(0.1)
                continue

            # perkecil di sisi kita, bukan minta kamera mengubah resolusi
            if self.a.max_width and frame.shape[1] > self.a.max_width:
                sc = self.a.max_width / frame.shape[1]
                frame = cv2.resize(frame, (self.a.max_width,
                                           int(frame.shape[0] * sc)))

            self.check_black(frame)

            # ambil kalibrasi terbaru SEBELUM zona dihitung, supaya
            # perubahan dari dashboard langsung berlaku di frame ini
            self.refresh_calibration()

            h, w = frame.shape[:2]
            zone = np.array([[int(x / 100 * w), int(y / 100 * h)]
                             for x, y in self.a.zone], np.int32)
            lamps_cfg = self.a.lamps

            now = time.time()
            if now - last >= interval:          # deteksi hanya N fps
                last = now
                boxes = self.det.detect(frame)
                n_in = 0
                for x1, y1, x2, y2, _ in boxes:
                    px, py = int((x1 + x2) / 2), int(y2)   # titik kaki
                    if cv2.pointPolygonTest(zone, (px, py), False) >= 0:
                        n_in += 1
                lamp_res = []
                if lamps_cfg:
                    if self._pembaca is None:
                        self._pembaca = buat_pembaca(lamps_cfg, INDIKATOR_DEMO)
                        for pb in self._pembaca:
                            pb.rekam_baseline(frame)
                    lamp_res, counts = read_tower(frame, self._pembaca, now)
                    with shared.lock:
                        shared.lamps = lamp_res
                        shared.counts = counts
                    self.push_machine_status(lamp_res)

                with shared.lock:
                    shared.people = n_in
                self.evaluate(n_in, now)

            if not self.a.no_overlay:
                with shared.lock:
                    last_lamps = shared.lamps
                if last_lamps:
                    draw_lamps(frame, last_lamps)
                self.draw(frame, zone, boxes)

            ok, buf = cv2.imencode(".jpg", frame,
                                   [cv2.IMWRITE_JPEG_QUALITY, self.a.quality])
            if ok:
                with shared.lock:
                    shared.jpeg = buf.tobytes()

            frames += 1
            if now - t_fps >= 1:
                with shared.lock:
                    shared.fps = frames / (now - t_fps)
                frames, t_fps = 0, now

        cap.release()

    # ---------- diagnosa ----------
    def check_black(self, frame):
        """Peringatkan sekali kalau frame praktis hitam semua."""
        if self.black_warned:
            return
        if float(frame.mean()) > 6:
            return
        self.black_warned = True
        log.warning("=" * 62)
        log.warning("FRAME HITAM — kamera terbuka tapi tidak mengirim gambar.")
        log.warning("Kemungkinan penyebab:")
        log.warning("  1. OBS (atau app lain) masih memegang kamera. Tutup OBS,")
        log.warning("     termasuk Virtual Camera-nya, lalu jalankan ulang.")
        log.warning("  2. Salah index device. Coba --device 0 atau --device 2.")
        log.warning("  3. Resolusi dipaksa ke ukuran yang tidak didukung.")
        log.warning("     Jangan pakai --width/--height.")
        log.warning("Jalankan 'python ai/webcam_demo.py --probe' untuk memeriksa.")
        log.warning("=" * 62)

    # ---------- overlay ----------
    def draw(self, frame, zone, boxes):
        h, w = frame.shape[:2]

        # zona
        overlay = frame.copy()
        cv2.fillPoly(overlay, [zone], (60, 130, 40))
        cv2.addWeighted(overlay, 0.18, frame, 0.82, 0, frame)
        cv2.polylines(frame, [zone], True, (80, 200, 80), 2)
        cv2.putText(frame, "ZONA LINE", (zone[0][0] + 8, zone[0][1] + 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (120, 230, 120), 1, cv2.LINE_AA)

        # kotak orang
        for x1, y1, x2, y2, c in boxes:
            px, py = int((x1 + x2) / 2), int(y2)
            inside = cv2.pointPolygonTest(zone, (px, py), False) >= 0
            col = (60, 60, 240) if inside else (150, 150, 150)
            cv2.rectangle(frame, (x1, y1), (x2, y2), col, 2)
            cv2.circle(frame, (px, py), 4, col, -1)      # titik kaki
            cv2.putText(frame, "person %.0f%%" % (c * 100), (x1, max(14, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, col, 1, cv2.LINE_AA)

        # status bar
        with shared.lock:
            n, fps, det = shared.people, shared.fps, shared.detector
            c = dict(shared.counts)
        absent = time.time() - self.last_seen
        txt = "%s | orang di zona: %d | %.0f fps" % (det, n, fps)
        if self.a.lamps:
            txt += "  ||  mesin: %d jalan  %d setting  %d stop  %d mati" % (
                c["run"], c["idle"], c["stop"], c["off"])
        cv2.rectangle(frame, (0, h - 26), (w, h), (18, 22, 30), -1)
        cv2.putText(frame, txt, (10, h - 8), cv2.FONT_HERSHEY_SIMPLEX,
                    0.48, (200, 210, 220), 1, cv2.LINE_AA)
        if absent > self.a.absent:
            cv2.putText(frame, "ALERT: operator tidak di area (%ds)" % absent,
                        (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (60, 60, 240), 2, cv2.LINE_AA)


# ---------------------------------------------------------------- http
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass                                    # jangan banjiri terminal

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/frame":                    # 1 gambar JPEG
            with shared.lock:
                data = shared.jpeg
            if data is None:
                self.send_error(503, "belum ada frame")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

        elif path == "/stream":                 # MJPEG bersambung
            self.send_response(200)
            self.send_header("Content-Type",
                             "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                while True:
                    with shared.lock:
                        data = shared.jpeg
                    if data:
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                         b"Content-Length: " +
                                         str(len(data)).encode() + b"\r\n\r\n" +
                                         data + b"\r\n")
                    time.sleep(1 / 15)
            except (BrokenPipeError, ConnectionResetError):
                pass

        elif path == "/status":
            with shared.lock:
                body = json.dumps({"people": shared.people,
                                   "fps": round(shared.fps, 1),
                                   "detector": shared.detector,
                                   "machines": shared.counts,
                                   "lamps": [{"no": l["no"], "status": l["status"],
                                              "indikator": l["indikator"]}
                                             for l in shared.lamps],
                                   "ready": shared.jpeg is not None}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)


# ---------------------------------------------------------------- main
def main():
    p = argparse.ArgumentParser(description="Webcam sebagai kamera uji")
    p.add_argument("--port", type=int, default=1984)
    p.add_argument("--device", type=int, default=1,
                   help="index webcam (0 sering dipakai OBS Virtual Camera, "
                        "webcam fisik biasanya 1)")
    p.add_argument("--source", default="",
                   help="sumber selain webcam: URL RTSP/HTTP atau path file video")
    p.add_argument("--loop", action="store_true",
                   help="ulangi dari awal kalau sumbernya file video")
    p.add_argument("--width", type=int, default=0,
                   help="paksa lebar frame (kosongkan kalau frame hitam)")
    p.add_argument("--height", type=int, default=0)
    p.add_argument("--max-width", type=int, default=960,
                   help="perkecil frame setelah ditangkap (hemat CPU)")
    p.add_argument("--no-dashboard-calibration", dest="dashboard_calibration",
                   action="store_false", default=True,
                   help="abaikan kalibrasi dashboard, pakai --zone/--lamp saja")
    p.add_argument("--no-overlay", action="store_true",
                   help="jangan gambar zona & kotak lampu ke dalam frame "
                        "(pakai kalau ingin menggambarnya di dashboard saja)")
    p.add_argument("--probe", action="store_true",
                   help="cek sumber lalu keluar: resolusi, kecerahan, deteksi")
    p.add_argument("--dashboard", default="http://127.0.0.1:8000")
    p.add_argument("--api-key", default="",
                   help="samakan dengan CCTV_API_KEY di dashboard")
    p.add_argument("--line-id", default="ajl-01",
                   help="line yang dipakai untuk mengirim alert")
    p.add_argument("--detector", choices=["yolo", "hog"], default="yolo")
    p.add_argument("--fps", type=int, default=4, help="fps deteksi")
    p.add_argument("--status-interval", type=float, default=5.0,
                   help="jeda kirim status mesin ke dasbor, detik. "
                        "Menentukan ketepatan waktu catatan episode; "
                        "pakai 1 saat mengumpulkan data uji")
    p.add_argument("--quality", type=int, default=70, help="kualitas JPEG")
    p.add_argument("--absent", type=int, default=15,
                   help="detik tanpa orang sebelum alert (demo: kecil)")
    p.add_argument("--crowd-min", type=int, default=2)
    p.add_argument("--crowd-seconds", type=int, default=5)
    p.add_argument("--cooldown", type=int, default=30)
    p.add_argument("--lamp", action="append", default=[],
                   help="ROI lampu tower satu mesin, persen: x,y,w,h . "
                        "Ulangi untuk tiap mesin: --lamp 5,5,6,10 --lamp 15,5,6,10")
    p.add_argument("--lamp-grid", type=int, default=0,
                   help="buat N ROI lampu otomatis berderet di bagian atas frame "
                        "(cara cepat menguji: --lamp-grid 10)")
    p.add_argument("--lamp-min-ratio", type=float, default=0.10,
                   help="proporsi piksel berwarna agar lampu dianggap MENYALA "
                        "(0-1). Inilah ambang yang disetel di lapangan: naikkan "
                        "kalau lampu padam terbaca menyala, turunkan kalau lampu "
                        "menyala terbaca padam. Nilai yang cocok disalin ke "
                        "lamp_min_ratio di ai/zones.json")
    p.add_argument("--zone", default="10,25,90,25,90,95,10,95",
                   help="polygon zona dalam persen: x1,y1,x2,y2,...")
    args = p.parse_args()

    nums = [float(v) for v in args.zone.split(",")]
    args.zone = list(zip(nums[0::2], nums[1::2]))

    # --- ROI lampu tower ---
    lamps = []
    for i, spec in enumerate(args.lamp):
        x, y, w, h = [float(v) for v in spec.split(",")]
        lamps.append({"no": i + 1, "lamp": [x, y, w, h]})
    if args.lamp_grid:                       # deret otomatis di bagian atas
        n = args.lamp_grid
        pad, top, lh = 4.0, 4.0, 14.0
        cw = (100 - pad * 2) / n
        for i in range(n):
            lamps.append({"no": i + 1,
                          "lamp": [pad + i * cw + cw * 0.15, top,
                                   cw * 0.7, lh]})
    args.lamps = lamps
    if args.dashboard_calibration:
        log.info("Kalibrasi diambil dari dashboard tiap 10 detik "
                 "(--zone/--lamp dipakai kalau belum ada kalibrasi)")

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-7s %(message)s")

    # PENTING: buka kamera di MAIN THREAD.
    # macOS hanya mengizinkan permintaan izin kamera dari main thread.
    if args.source:
        log.info("Membuka sumber: %s", args.source)
        vc = cv2.VideoCapture(args.source, cv2.CAP_FFMPEG)
    else:
        log.info("Membuka webcam (device %d) ...", args.device)
        log.info("  device 0 = OBS Virtual Camera (kalau OBS terpasang), "
                 "device 1 = webcam fisik")
        vc = cv2.VideoCapture(args.device)
        # JANGAN paksa resolusi kecuali diminta: di macOS, meminta ukuran yang
        # tidak didukung webcam membuat AVFoundation mengembalikan frame hitam.
        if args.width and args.height:
            vc.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
            vc.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)

    if not vc.isOpened():
        if args.source:
            raise SystemExit("Sumber tidak bisa dibuka: %s" % args.source)
        raise SystemExit(
            "Webcam device %d tidak bisa dibuka.\n\n"
            "Coba index lain: --device 0, --device 2, dst.\n"
            "  (0 biasanya OBS Virtual Camera kalau OBS terpasang)\n\n"
            "Di macOS: buka System Settings > Privacy & Security > Camera,\n"
            "lalu izinkan aplikasi terminal yang Anda pakai. Setelah itu\n"
            "TUTUP dan buka lagi terminalnya (izin baru berlaku setelah restart)."
            % args.device)

    if args.probe:
        det = build_detector(args.detector)
        log.info("Detektor: %s", det.name)
        for i in range(15):
            ok, f = vc.read()
            if not ok:
                log.error("frame %d: GAGAL dibaca", i)
                continue
            mean = float(f.mean())
            note = "  <-- HITAM" if mean < 6 else ""
            if i >= 12:
                boxes = det.detect(f)
                log.info("frame %2d: %dx%d  kecerahan %.1f  orang terdeteksi: %d%s",
                         i, f.shape[1], f.shape[0], mean, len(boxes), note)
            else:
                log.info("frame %2d: %dx%d  kecerahan %.1f%s",
                         i, f.shape[1], f.shape[0], mean, note)
        vc.release()
        log.info("")
        log.info("Kecerahan < 6 = frame hitam. Kalau semua hitam: tutup OBS,")
        log.info("atau coba --device 0 / --device 2.")
        return

    cap = Capture(args, vc)
    cap.start()

    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    log.info("Bridge siap di http://127.0.0.1:%d", args.port)
    log.info("  /frame   1 gambar JPEG      (untuk CCTV_STREAM_MODE=img)")
    log.info("  /stream  MJPEG bersambung   (untuk CCTV_STREAM_MODE=img juga)")
    log.info("  /status  jumlah orang & fps")
    log.info("Alert dikirim ke %s untuk line '%s'", args.dashboard, args.line_id)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log.info("berhenti")
        cap.stop_flag.set()
        srv.shutdown()


if __name__ == "__main__":
    main()
