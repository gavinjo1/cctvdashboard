#!/usr/bin/env python3
"""AI worker — deteksi kejadian dari CCTV, kirim alert ke dashboard.

Jalankan terpisah dari dashboard (boleh di mesin lain yang punya GPU):

    python ai/worker.py --config ai/zones.json

Berkas ini MERANGKAI, bukan mengerjakan sendiri. Tiap tanggung jawab
tinggal di berkasnya masing-masing supaya bisa diubah dan diuji terpisah:

    ai/lamp.py      baca menara lampu: segmen mana menyala, warnanya apa
    ai/aturan.py    kapan sesuatu layak dilaporkan  (tanpa jaringan)
    ai/pengirim.py  bicara HTTP ke dashboard        (tanpa aturan)
    ai/kamera.py    tarik stream, sambung ulang     (tanpa penglihatan)
    ai/worker.py    ^ merangkai semuanya, satu thread per kamera

Yang dideteksi:
  1. Operator tidak di area   — tidak ada orang di zona line > absent_seconds
  2. Kerumunan di line        — >= crowd_min orang di zona > crowd_seconds
  3. Mesin stop tanpa penanganan — indikator masalah menyala > response_seconds
                                   dan tidak ada orang di zona

Menara lampu dibaca berdasarkan POSISI segmen, bukan warna; "semua padam"
berarti mesin jalan normal. Lihat ai/lamp.py.
"""
import argparse
import json
import logging
import sys
import threading
import time
from pathlib import Path

import cv2
import numpy as np

# OpenCV menulis peringatan C++ langsung ke stderr, di luar logging Python:
#   "[ WARN:4@188.7] global cap.cpp:217 open VIDEOIO(FFMPEG): backend is
#    generally available but can't be used to capture by name"
# Isinya sudah dilaporkan ulang oleh log kita sendiri dengan nama line yang
# jelas, jadi baris itu hanya menutupi pesan yang berguna.
cv2.setLogLevel(0)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aturan import AturanAlert, Kehadiran          # noqa: E402
from kamera import Kamera, Pengatur                # noqa: E402
from lamp import buat_pembaca, read_tower          # noqa: E402
from pengirim import Pengirim                      # noqa: E402

try:
    from ultralytics import YOLO
except ImportError:
    sys.exit("ultralytics belum terpasang. "
             "Jalankan: pip install -r ai/requirements.txt")

log = logging.getLogger("ai")

PERSON_CLASS = 0          # class 'person' pada model COCO

# Pemuatan model digilir: dua thread yang memuat bobot yang sama berbarengan
# sempat menabrak langkah fuse di ultralytics.
_KUNCI_MUAT = threading.Lock()

#: jeda antar laporan "warna tidak cocok" per kamera (detik). Kalau kotaknya
#: memang meleset, keadaannya menetap — tidak perlu diulang tiap frame.
LAPOR_WARNA_JANGGAL = 300

#: Urutan segmen menara ATAS -> BAWAH pada Toyota JAT810 di pabrik ini.
#: Dipastikan 15 Sep 2026 dari layar "Signal lamp" mesin (4 kolom lampu:
#: merah, hijau, putih, kuning) dan dari vid8.mp4 — menara di sana
#: menyalakan segmen ke-2 (hijau) dan ke-3 (putih), yang cocok dengan
#: urutan kolom itu.
#:
#: Arti yang SUDAH dipastikan operator:
#:   semua padam  mesin jalan normal   (karena itu SAAT_GELAP_BAWAAN="run")
#:   hijau        benang PAKAN putus
#:   merah        benang LUSI putus
#:
#: TODO(pabrik): putih dan kuning BELUM dipastikan. Layar Signal lamp
#: menunjukkan putih dipakai CLOTH DOFFING dan kuning dipakai WARP OUT /
#: FOREMAN CALL ON, tetapi fotonya miring dan buram — jangan dipercaya
#: sebelum dibaca langsung di layar mesin.
INDIKATOR_BAWAAN = ["lusi_putus", "pakan_putus", "putih", "kuning"]

#: TODO(pabrik): SATU WARNA DIPAKAI BANYAK SEBAB. Di layar Signal lamp,
#: merah muncul untuk M/C TROUBLE, WASTE-SELVAGE STOP, dan FULL-LENO
#: SELVAGE STOP sekaligus. Kamera hanya bisa melaporkan "merah menyala",
#: bukan sebab persisnya. Sebab yang tepat harus datang dari data mesin
#: (Modbus/OPC-UA), bukan dari lampu.
PERAN_BAWAAN = {
    "lusi_putus": "masalah",
    "pakan_putus": "masalah",
    "putih": "setup",       # TODO(pabrik): doffing = berhenti terencana?
    "kuning": "masalah",    # TODO(pabrik): panggil foreman = masalah?
}

#: Status mesin saat TIDAK ADA segmen menyala. Lihat catatan di ai/lamp.py —
#: ini harus dipastikan dengan melihat mesin yang sedang berproduksi.
#: TODO(pabrik): "run" bila menara padam saat jalan, "off" bila hijau
#: seharusnya menyala terus.
SAAT_GELAP_BAWAAN = "run"

#: jeda sebelum menyambung ulang stream yang putus (detik)
RECONNECT_DELAY = 3

#: kegagalan memproses frame berturut-turut sebelum stream disambung ulang
ERROR_BURST = 10

#: kamera dianggap macet bila sekian detik tidak menghasilkan frame
STALE_AFTER = 60

#: jeda pemeriksaan kesehatan semua kamera oleh supervisor di main()
SUPERVISE_INTERVAL = 15


# ---------------------------------------------------------------- util
def poly_from_percent(points, w, h):
    """Ubah polygon persen -> piksel."""
    return np.array([[int(x / 100 * w), int(y / 100 * h)]
                     for x, y in points], np.int32)


def box_center_bottom(box):
    """Titik acuan orang = tengah-bawah bounding box (posisi kaki di lantai)."""
    x1, y1, x2, y2 = box
    return (int((x1 + x2) / 2), int(y2))


# ---------------------------------------------------------------- worker
class CameraWorker(threading.Thread):
    """Satu thread per kamera: rangkai kamera, penglihatan, aturan, pengirim."""

    def __init__(self, cam_cfg, defaults, dashboard_url, model_path):
        super().__init__(daemon=True, name=cam_cfg["line_id"])
        self.cfg = {**defaults, **cam_cfg}
        self.line_id = cam_cfg["line_id"]
        self.stop_flag = threading.Event()

        self.kamera = Kamera(cam_cfg["rtsp"], self.line_id, self.stop_flag)
        self.pengirim = Pengirim(dashboard_url, self.line_id,
                                 self.cfg.get("api_key") or "",
                                 self.cfg["cooldown_seconds"])
        self.aturan = AturanAlert(self.cfg, time.time())
        #: detik tanpa deteksi sebelum operator dianggap benar-benar pergi.
        #: Terukur di rekaman pabrik: celah deteksi terpanjang 8,2 detik
        #: sementara orangnya masih di tempat.
        self.kehadiran = Kehadiran(self.cfg.get("jeda_orang", 12))

        self.model_path = model_path
        self.model = None       # dimuat di thread-nya sendiri, lihat run()

        # ---- menara lampu ----
        self.lamps = self.cfg.get("machines", [])   # kotak menara per mesin
        self.indikator = self.cfg.get("indikator", INDIKATOR_BAWAAN)
        self.warna_harapan = self.cfg.get("warna_menara")
        # PembacaMenara menyimpan riwayat kedip, jadi HARUS dipakai ulang
        # antar frame. Dibangun ulang hanya kalau kalibrasi berubah.
        self.pembaca = self._buat_pembaca()
        self.baseline_siap = False
        self.warna_terakhir_lapor = 0.0

        # ---- zona line ----
        self.zone = self.cfg.get("zone", [])
        self.zone_px = None
        self._warned_zone = False

        # ---- penjadwalan kiriman & kalibrasi ----
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

    def _buat_pembaca(self):
        return buat_pembaca(self.lamps, self.indikator,
                            self.cfg.get("ambang_lampu"), self.warna_harapan)

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

        cal = self.pengirim.ambil_kalibrasi()
        if cal is None:
            return

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
            self.pembaca = self._buat_pembaca()
            self.baseline_siap = False
        elif self.lamps:
            log.warning("[%s] kalibrasi dashboard tidak berisi ROI lampu — "
                        "%d ROI dari zones.json tetap dipakai. Untuk benar-benar "
                        "mengosongkannya, hapus juga dari zones.json.",
                        self.line_id, len(self.lamps))

        log.info("[%s] kalibrasi diperbarui dari dashboard: "
                 "%d titik zona, %d ROI lampu",
                 self.line_id, len(zone), len(self.lamps))

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

    # ---------- penglihatan ----------
    def hitung_orang(self, frame):
        """Jumlah orang yang KAKINYA di dalam zona line."""
        res = self.model.predict(
            frame, classes=[PERSON_CLASS], verbose=False,
            conf=self.cfg["min_confidence"])[0]
        n = 0
        for box in res.boxes.xyxy.cpu().numpy():
            px, py = box_center_bottom(box)
            if cv2.pointPolygonTest(self.zone_px, (px, py), False) >= 0:
                n += 1
        return n

    def baca_lampu(self, frame, now):
        """Baca semua menara. Return jumlah mesin yang berstatus stop."""
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
            peran=self.cfg.get("peran_indikator", PERAN_BAWAAN),
            saat_gelap=self.cfg.get("saat_gelap", SAAT_GELAP_BAWAAN))

        self._lapor_warna_janggal(results, now)
        self._kirim_status(results, now)
        return counts["stop"]

    def _lapor_warna_janggal(self, results, now):
        """Kotak menara yang meleset TIDAK terlihat dari angka ukurnya.

        Pembacaannya tetap masuk akal, cuma artinya salah. Warna piksel
        adalah satu-satunya cara menangkapnya tanpa melihat gambar.
        """
        janggal = [(r["no"], r["warna_janggal"]) for r in results
                   if r.get("warna_janggal")]
        if not janggal or now - self.warna_terakhir_lapor < LAPOR_WARNA_JANGGAL:
            return
        self.warna_terakhir_lapor = now
        log.warning("[%s] WARNA TIDAK COCOK di mesin %s — kotak menara "
                    "kemungkinan meleset; arti dari posisi jadi salah "
                    "meski angkanya wajar. Periksa kalibrasi.",
                    self.line_id,
                    ", ".join("%s%s" % (no, ind) for no, ind in janggal))

    def _kirim_status(self, results, now):
        if now - self.last_status_push < self.cfg.get("status_interval", 10):
            return
        self.last_status_push = now
        self.pengirim.kirim_status(results, self.kehadiran.jumlah,
                                   self.kehadiran.sejak)

    # ---------- pemrosesan satu frame ----------
    def process_frame(self, frame, now, deteksi_orang=True):
        """Baca lampu, deteksi orang (lebih jarang), lalu jalankan aturan."""
        self.refresh_calibration()

        h, w = frame.shape[:2]
        if self.zone_px is None:
            self.zone_px = self.zone_polygon(w, h)
        zone_ok = self.zone_px is not None

        if zone_ok and deteksi_orang:
            n_in_zone = self.kehadiran.perbarui(self.hitung_orang(frame), now)
        else:
            # Frame antara: pakai hitungan orang terakhir. Aturan alert bekerja
            # pada rentang menit, jadi jeda beberapa ratus milidetik tidak
            # mengubah hasilnya — sementara membaca lampu tiap frame WAJIB.
            n_in_zone = self.kehadiran.jumlah

        n_stop = self.baca_lampu(frame, now) if self.pembaca else 0

        for a in self.aturan.nilai(n_in_zone, n_stop, now, zone_ok=zone_ok):
            self.pengirim.kirim_alert(*a.sebagai_argumen())

    # ---------- satu sesi koneksi ----------
    def session(self):
        """Tarik stream sampai putus. Kembali = perlu disambung ulang."""
        cap = self.kamera.buka()
        if cap is None:
            return                      # Kamera sudah menunggu jeda mundurnya

        self.zone_px = None
        jadwal = Pengatur(self.cfg["fps"], self.cfg.get("fps_lampu"))
        beruntun = 0                    # kegagalan frame berturut-turut

        try:
            while not self.stop_flag.is_set():
                if not cap.grab():      # buang frame, jangan decode semua
                    log.warning("[%s] stream terputus", self.line_id)
                    return

                now = time.time()
                # ada_menara dihitung ulang tiap putaran: kalibrasi bisa
                # datang di tengah sesi dan menara baru muncul tanpa restart
                if not jadwal.perlu_proses(now, bool(self.pembaca)):
                    continue

                ok, frame = cap.retrieve()
                if not ok or frame is None:
                    continue

                try:
                    self.process_frame(frame, now,
                                       deteksi_orang=jadwal.perlu_orang(now))
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

        SATU MODEL PER KAMERA, dimuat di sini. Satu objek YOLO yang dipakai
        bersama beberapa thread menabrak langkah fuse di ultralytics
        ('Conv' object has no attribute 'bn') pada frame pertama.
        """
        with _KUNCI_MUAT:
            self.model = YOLO(self.model_path)
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
def muat_config(jalur, no_dashboard_cal):
    """Baca zones.json, isi nilai bawaan, kembalikan (cfg, cameras, defaults)."""
    cfg_path = Path(jalur)
    if not cfg_path.exists():
        sys.exit(f"Config tidak ditemukan: {cfg_path}\n"
                 f"Salin dari ai/zones.example.json lalu sesuaikan.")
    cfg = json.loads(cfg_path.read_text())

    cameras = cfg.get("cameras", [])
    if not cameras:
        sys.exit("Tidak ada kamera di config.")

    defaults = cfg.get("defaults", {})
    if no_dashboard_cal:
        defaults["use_dashboard_calibration"] = False
    else:
        log.info("Kalibrasi diambil dari dashboard tiap %ds "
                 "(zones.json dipakai sebagai cadangan)",
                 defaults.get("calibration_interval", 30))

    api_key = cfg.get("api_key", "")
    if not api_key:
        log.warning("api_key kosong di config — dashboard akan menolak "
                    "kiriman alert & status mesin kalau CCTV_API_KEY diisi.")
    for cam in cameras:
        cam.setdefault("api_key", api_key)

    return cfg, cameras, defaults


def awasi(workers, cameras, defaults, dashboard_url, model):
    """Supervisor: kamera yang mati atau macet HARUS terlihat.

    Sebelum ini, thread yang berhenti meninggalkan proses tetap hidup tanpa
    sepatah kata pun di log, dan line itu berhenti terpantau tanpa ada yang
    tahu.
    """
    stale_after = defaults.get("stale_after_seconds", STALE_AFTER)
    macet = set()
    while True:
        time.sleep(SUPERVISE_INTERVAL)
        for i, w in enumerate(workers):
            h = w.health()
            line_id = h["line_id"]

            if not h["alive"]:
                log.error("[%s] thread berhenti setelah %d frame — "
                          "dijalankan ulang", line_id, h["frames"])
                baru = CameraWorker(cameras[i], defaults, dashboard_url, model)
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


def main():
    ap = argparse.ArgumentParser(description="AI worker CCTV -> dashboard")
    ap.add_argument("--config", default="ai/zones.json",
                    help="file konfigurasi JSON")
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

    cfg, cameras, defaults = muat_config(args.config,
                                         args.no_dashboard_calibration)
    log.info("Model %s — satu salinan per kamera", args.model)

    workers = [CameraWorker(cam, defaults, cfg["dashboard_url"], args.model)
               for cam in cameras]
    for w in workers:
        w.start()
    log.info("%d kamera dipantau. Ctrl+C untuk berhenti.", len(workers))

    try:
        awasi(workers, cameras, defaults, cfg["dashboard_url"], args.model)
    except KeyboardInterrupt:
        log.info("Menghentikan worker...")
        for w in workers:
            w.stop()
        for w in workers:
            w.join(timeout=5)


if __name__ == "__main__":
    main()
