#!/usr/bin/env python3
"""Sajikan FOTO atau VIDEO sebagai kamera, satu sumber per line.

Satu proses melayani BANYAK line sekaligus — berbeda dari webcam_demo.py yang
hanya satu line per proses. Dashboard memakai satu template URL untuk semua
line, jadi semua sumber harus datang dari satu server.

    ai/foto/ajl-01.png   ->  http://127.0.0.1:1984/frame?src=ajl-01
    ai/foto/ajl-02.mp4   ->  http://127.0.0.1:1984/frame?src=ajl-02

Nama berkas = line_id. Kalau nama berkasnya lain, petakan sendiri:

    --map ajl-01=vid1.mp4 --map ajl-02=vid2.mp4

Untuk banyak line, jangan mengetik --map berkali-kali. Tulis di
<dir>/peta.txt, satu baris per line, dan jalankan tanpa --map:

    ajl-01 = vid1.mp4
    ajl-02 = vid2.mp4

Jalankan:

    python ai/foto_kamera.py --dir ai/foto

Lalu dashboard di terminal lain:

    ./run-demo.sh

FOTO dibaca ulang tiap permintaan — timpa berkasnya, muat ulang halaman,
tanpa restart. VIDEO diputar berulang di utas sendiri dengan KECEPATAN ASLI;
tiap line punya pemutarnya masing-masing.

Sumber non-16:9 otomatis diberi bilah gelap. Tanpa itu, kanvas kalibrasi
(seukuran kotak kamera) dan pembacaan ROI (memetakan persen ke gambar asli)
memakai kerangka berbeda — kotak yang digambar di lampu akan dibaca dari
tempat lain.
"""
import argparse
import logging
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import cv2
import numpy as np

log = logging.getLogger("foto")

# Pemuatan model digilir: dua utas yang memuat berkas bobot yang sama
# berbarengan sempat menabrak langkah fuse di ultralytics.
_KUNCI_MUAT = threading.Lock()

EKST_FOTO = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
EKST_VIDEO = (".mp4", ".mov", ".avi", ".mkv", ".m4v")
BERKAS_PETA = "peta.txt"


def ke_rasio(gambar, rasio):
    """Beri bilah gelap agar berasio sama dengan kotak kamera."""
    if not rasio:
        return gambar
    h, w = gambar.shape[:2]
    if abs((w / float(h)) - rasio) < 0.01:
        return gambar
    if w / float(h) < rasio:                  # terlalu tinggi -> lebarkan
        baru_w, baru_h = int(round(h * rasio)), h
    else:                                     # terlalu lebar -> tinggikan
        baru_w, baru_h = w, int(round(w / rasio))
    kanvas = np.full((baru_h, baru_w, 3), 12, np.uint8)
    x, y = (baru_w - w) // 2, (baru_h - h) // 2
    kanvas[y:y + h, x:x + w] = gambar
    return kanvas


class PemutarVideo(threading.Thread):
    """Putar satu video berulang, simpan frame terbaru sebagai JPEG.

    Diputar dengan KECEPATAN ASLI. Tanpa penahan laju, cap.read() melahap
    secepat disk: video 10 menit habis dalam hitungan detik, dan seluruh
    perhitungan waktu kejadian ikut terkompresi.
    """

    def __init__(self, path: Path, rasio, mutu=80):
        super().__init__(daemon=True, name=path.stem)
        self.path = path
        self.rasio = rasio
        self.mutu = mutu
        self.stop = threading.Event()
        self._lock = threading.Lock()
        self._jpeg = b""
        self._mentah = None          # frame mentah terakhir, untuk pelacak
        self.fps = 0.0
        self.frames = 0
        self.pelacak = None          # diisi setelah dibuat, kalau --lacak

    def run(self):
        while not self.stop.is_set():
            cap = cv2.VideoCapture(str(self.path))
            if not cap.isOpened():
                log.error("[%s] tidak bisa dibuka", self.path.name)
                return
            self.fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            jeda = 1.0 / self.fps
            berikut = time.time()
            while not self.stop.is_set():
                ok, f = cap.read()
                if not ok:
                    break                     # habis -> ulang dari awal
                with self._lock:
                    self._mentah = f
                tampil = ke_rasio(f, self.rasio)
                if self.pelacak is not None:
                    tampil = self.pelacak.gambar(tampil)
                ok2, buf = cv2.imencode(".jpg", tampil,
                                        [cv2.IMWRITE_JPEG_QUALITY, self.mutu])
                if ok2:
                    with self._lock:
                        self._jpeg = buf.tobytes()
                        self.frames += 1
                berikut += jeda
                sisa = berikut - time.time()
                if sisa > 0:
                    self.stop.wait(sisa)
                else:
                    berikut = time.time()     # ketinggalan: jangan menumpuk
            cap.release()

    def jpeg(self):
        with self._lock:
            return self._jpeg

    def jpeg_ke(self):
        """(jpeg, nomor_frame) — nomornya dipakai MJPEG agar tidak mengirim
        frame yang sama berulang kali."""
        with self._lock:
            return self._jpeg, self.frames

    def mentah(self):
        with self._lock:
            return self._mentah


def baca_peta(folder: Path):
    """Baca peta line->berkas dari <folder>/peta.txt.

    Ada supaya menjalankan 9 line tidak berarti mengetik 9 kali --map.
    Nama berkas dibiarkan apa adanya (vid4.mp4, bukan ajl-04.mp4) — kalau
    berkasnya harus diganti nama agar terbaca, jejak asalnya hilang dan
    menambah sumber baru berarti menata ulang seluruh folder.
    """
    berkas = folder / BERKAS_PETA
    peta = {}
    if not berkas.exists():
        return peta
    for no, baris in enumerate(berkas.read_text().splitlines(), 1):
        baris = baris.split("#", 1)[0].strip()
        if not baris:
            continue
        if "=" not in baris:
            log.warning("%s baris %d diabaikan, tidak ada '=': %s",
                        BERKAS_PETA, no, baris)
            continue
        line_id, nama = baris.split("=", 1)
        peta[line_id.strip()] = nama.strip()
    log.info("%s: %d pemetaan", BERKAS_PETA, len(peta))
    return peta


class Pelacak(threading.Thread):
    """Deteksi + lacak orang pada satu sumber, dengan laju sendiri.

    DUA LAJU. Video ditampilkan ~23 fps, tetapi YOLO ~137 ms per frame di CPU —
    mendeteksi tiap frame tampilan akan memakan satu inti penuh per kamera.
    Jadi pelacakan berjalan lebih lambat (bawaan 6 fps) dan kotak terakhirnya
    digambar ulang di setiap frame tampilan. Orang berjalan hanya bergeser
    ~23 cm antar deteksi pada laju itu — masih jauh di bawah lebar tubuh.

    MEMBEDAKAN ORANG DARI MESIN ITU KERJA DETEKTORNYA, BUKAN KERJA FILTER.
    Percobaan pada vid1.mp4 (60 detik, 6 fps):

        yolov8n 640   10 jejak — termasuk bagian mesin gelap (conf 0.46),
                      dan satu operator jauh pecah jadi 3 id
        yolov8n 1280   7 jejak, 218 ms/frame — conf masih tumpang tindih
        yolov8s 640    6 jejak, 137 ms/frame — orang 0.61-0.83, sisanya 0.43

    Hanya yolov8s yang memisahkan lewat CONFIDENCE. Karena itu bawaannya
    yolov8s dengan ambang 0.50, bukan yolov8n dengan filter perpindahan.

    PENYARINGAN LEWAT PERPINDAHAN (--min-geser) BAWAANNYA MATI. Pernah
    dipasang 25 px dengan alasan "benda diam tidak bergerak", lalu diukur:
    bagian mesin yang salah dikenali bergeser 23.8 px dalam 5 detik,
    sementara tiga operator yang berdiri melayani mesin hanya bergeser
    3.5, 9.3, dan 16 px. Jadi filter itu membuang operator sungguhan lebih
    sering daripada membuang mesin. Flag-nya ditinggalkan untuk kamera yang
    sudut dan isinya lain, tetapi jangan dinyalakan tanpa mengukur dulu.
"""

    def __init__(self, line_id, pemutar, model_path, fps_lacak=6.0,
                 jeda_keluar=5.0, min_conf=0.50, min_geser=0.0,
                 min_hadir=3.0, on_event=None):
        super().__init__(daemon=True, name="lacak-" + line_id)
        self.line_id = line_id
        self.pemutar = pemutar
        self.model_path = model_path
        self.model = None            # dimuat di utasnya sendiri, lihat run()
        self.jeda = 1.0 / max(0.5, fps_lacak)
        self.jeda_keluar = jeda_keluar
        self.min_conf = min_conf
        self.min_geser = min_geser
        self.min_hadir = min_hadir
        self.on_event = on_event
        self._riwayat = {}           # id -> [posisi awal, geser_maks, lolos?]
        self.stop = threading.Event()
        self._lock = threading.Lock()
        self._kotak = []             # [(x1,y1,x2,y2)] hasil deteksi terakhir
        self._kotak_pada = 0.0       # kapan kotak itu didapat
        self._diam = 0               # deteksi yang ditolak karena tidak bergerak
        self.hadir = False
        self.masuk_pada = None       # calon waktu masuk, dipakai juga di overlay
        self.tercatat = False        # calon sudah lolos min_hadir?
        self.terlihat_pada = 0.0
        self.kejadian = []           # [{masuk, keluar, durasi}]

    # ---------- gambar ----------
    def gambar(self, frame):
        with self._lock:
            kotak = list(self._kotak)
            umur = time.time() - self._kotak_pada
            hadir = self.hadir
            sejak = self.masuk_pada
            diam = self._diam
        # Kotak DITAHAN di antara deteksi. YOLO sesekali melewatkan satu siklus
        # (terukur: 6 dari 10 cuplikan ada kotak), dan kalau kotak ikut hilang
        # tiap kali, kotaknya berkedip-kedip padahal operatornya jelas terlihat.
        # Ditahan selama kehadiran masih berlaku, digambar redup supaya terlihat
        # bahwa posisinya sudah agak lama.
        if not hadir or umur > self.jeda_keluar:
            kotak = []
        warna_kotak = (90, 220, 255) if umur <= 2 * self.jeda else (70, 150, 190)
        for x1, y1, x2, y2 in kotak:
            cv2.rectangle(frame, (x1, y1), (x2, y2), warna_kotak, 2)
        if hadir and sejak:
            lama = int(time.time() - sejak)
            teks = "OPERATOR MASUK %s (%dm %02dd)  -  %d orang%s" % (
                datetime.fromtimestamp(sejak).strftime("%H.%M"),
                lama // 60, lama % 60, len(kotak),
                "  [%d benda diam diabaikan]" % diam if diam else "")
            warna = (90, 220, 120)
        else:
            teks = "TIDAK ADA OPERATOR" + (
                "  [%d benda diam diabaikan]" % diam if diam else "")
            warna = (150, 150, 150)
        h = frame.shape[0]
        cv2.rectangle(frame, (0, h - 22), (frame.shape[1], h), (22, 26, 32), -1)
        cv2.putText(frame, teks, (8, h - 7), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, warna, 1, cv2.LINE_AA)
        return frame

    # ---------- kehadiran ----------
    def _perbarui_kehadiran(self, ada_orang, now):
        if ada_orang:
            self.terlihat_pada = now
            if not self.hadir:
                self.hadir = True
                self.masuk_pada = now
                self.tercatat = False
            elif not self.tercatat and (now - self.masuk_pada) >= self.min_hadir:
                # Baru dicatat setelah bertahan min_hadir, tapi dengan waktu
                # SAAT PERTAMA TERLIHAT — supaya jamnya tetap benar.
                self.tercatat = True
                self._catat("masuk", self.masuk_pada)
        elif self.hadir and (now - self.terlihat_pada) >= self.jeda_keluar:
            # Jeda ini penting: deteksi sering putus sesaat saat operator
            # terhalang mesin. Tanpa jeda, satu frame kosong sudah dianggap
            # keluar, dan catatannya penuh masuk-keluar palsu.
            self.hadir = False
            if self.tercatat:
                self._catat("keluar", self.terlihat_pada)
            # Kalau belum tercatat: lintasan sekejap (orang lewat, atau kotak
            # yang salah lolos saring). Dibuang diam-diam, bukan kejadian.
            self.tercatat = False

    def _catat(self, jenis, ts):
        if jenis == "masuk":
            self.kejadian.append({"line_id": self.line_id, "masuk": ts,
                                  "keluar": None, "durasi": None})
        else:
            for k in reversed(self.kejadian):
                if k["keluar"] is None:
                    k["keluar"] = ts
                    k["durasi"] = round(ts - k["masuk"], 1)
                    break
        k = self.kejadian[-1] if self.kejadian else None
        log.info("[%s] operator %s  %s%s", self.line_id, jenis.upper(),
                 datetime.fromtimestamp(ts).strftime("%H.%M.%S"),
                 "  (di area %.0f detik)" % k["durasi"]
                 if jenis == "keluar" and k and k["durasi"] else "")
        if self.on_event:
            self.on_event(self.line_id, jenis, ts)

    # ---------- loop ----------
    def run(self):
        # SATU MODEL PER LINE, bukan satu model dipakai bersama. track(persist=
        # True) menyimpan state pelacak DI DALAM objek model: kalau dua line
        # memakai objek yang sama, lintasan ajl-01 dan ajl-02 masuk ke pelacak
        # yang sama dan id-nya saling mencemari. Memuatnya di sini juga
        # menghindari dua utas menyatukan (fuse) satu model berbarengan.
        from ultralytics import YOLO
        with _KUNCI_MUAT:
            self.model = YOLO(self.model_path)
        while not self.stop.is_set():
            t0 = time.time()
            frame = self.pemutar.mentah()
            if frame is None:
                self.stop.wait(0.2)
                continue
            try:
                r = self.model.track(frame, persist=True, verbose=False,
                                     classes=[0], conf=self.min_conf,
                                     tracker="bytetrack.yaml")[0]
            except Exception:
                log.exception("[%s] deteksi gagal pada satu frame", self.line_id)
                self.stop.wait(self.jeda)
                continue

            kotak, diam = [], 0
            if r.boxes is not None and len(r.boxes) and r.boxes.id is not None:
                xy = r.boxes.xyxy.cpu().numpy()
                ids = r.boxes.id.cpu().numpy().astype(int)
                sk = self.pemutar.rasio
                H, W = frame.shape[:2]
                # frame tampilan mungkin diberi bilah; geser kotaknya
                dx = dy = 0
                if sk:
                    tampil = ke_rasio(frame, sk)
                    dx = (tampil.shape[1] - W) // 2
                    dy = (tampil.shape[0] - H) // 2
                for (x1, y1, x2, y2), tid in zip(xy, ids):
                    tid = int(tid)
                    if self.min_geser > 0:
                        cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                        ax, ay, gmax, lolos = self._riwayat.get(
                            tid, (cx, cy, 0.0, False))
                        gmax = max(gmax, float(np.hypot(cx - ax, cy - ay)))
                        lolos = lolos or gmax >= self.min_geser
                        self._riwayat[tid] = [ax, ay, gmax, lolos]
                        if not lolos:
                            diam += 1
                            continue
                    kotak.append((int(x1) + dx, int(y1) + dy,
                                  int(x2) + dx, int(y2) + dy))
                # buang riwayat jejak yang sudah hilang, jangan menumpuk
                hidup = set(int(t) for t in ids)
                for t in list(self._riwayat):
                    if t not in hidup:
                        self._riwayat.pop(t, None)

            now = time.time()
            with self._lock:
                # kotak kosong TIDAK menghapus kotak lama — lihat gambar()
                if kotak:
                    self._kotak = kotak
                    self._kotak_pada = time.time()
                self._diam = diam
            self._perbarui_kehadiran(bool(kotak), now)

            sisa = self.jeda - (time.time() - t0)
            if sisa > 0:
                self.stop.wait(sisa)


class Katalog:
    """Peta line_id -> sumber (berkas foto, atau PemutarVideo)."""

    def __init__(self, folder: Path, rasio, peta_manual=None,
                 model_path=None, fps_lacak=6.0, jeda_keluar=5.0,
                 min_geser=0.0, min_hadir=3.0, min_conf=0.50,
                 lacak_line="*"):
        self.folder = folder
        self.rasio = rasio
        self.model_path = model_path
        self.fps_lacak = fps_lacak
        self.jeda_keluar = jeda_keluar
        self.min_geser = min_geser
        self.min_hadir = min_hadir
        self.min_conf = min_conf
        self.lacak_line = lacak_line          # "*" = semua, atau set line_id
        self._pemutar = {}                    # path -> PemutarVideo (dibagi)
        self.bersama = {}                     # path -> line_id yang menumpang
        self.pelacak = {}                     # line_id -> Pelacak
        self.foto = {}                        # line_id -> Path
        self.video = {}                       # line_id -> PemutarVideo
        self._pindai(peta_manual or {})

    def _pindai(self, peta_manual):
        # Berkas yang sudah dipetakan manual tidak boleh ikut terdaftar lagi
        # lewat penamaan otomatis — kalau tidak, satu video diputar oleh DUA
        # utas sekaligus, memakan CPU dua kali lipat tanpa guna.
        terpakai = set()

        # pemetaan eksplisit menang atas penamaan otomatis
        for line_id, nama in peta_manual.items():
            p = Path(nama) if Path(nama).is_absolute() else self.folder / nama
            if not p.exists():
                log.warning("--map %s=%s : berkas tidak ada", line_id, nama)
                continue
            self._daftar(line_id, p)
            terpakai.add(p.resolve())

        for p in sorted(self.folder.iterdir()):
            if not p.is_file():
                continue
            lid = p.stem
            if lid in self.foto or lid in self.video:
                continue                      # line ini sudah dipetakan
            if p.resolve() in terpakai:
                continue                      # berkasnya sudah dipakai line lain
            if p.suffix.lower() in EKST_FOTO + EKST_VIDEO:
                self._daftar(lid, p)

    def _daftar(self, line_id, p: Path):
        if p.suffix.lower() in EKST_VIDEO:
            # SATU BERKAS BOLEH DIPAKAI BEBERAPA LINE, tetapi hanya SATU
            # pemutar. Pabrik punya 18 line sementara rekamannya cuma
            # segelintir; kalau tiap line membuat pemutarnya sendiri, satu
            # berkas didekode 2-3 kali sekaligus tanpa guna. Line yang
            # berbagi menampilkan gambar yang sama persis — itu memang
            # penambal demo, bukan kamera terpisah.
            kunci = p.resolve()
            pv = self._pemutar.get(kunci)
            if pv is not None:
                self.video[line_id] = pv
                self.bersama.setdefault(kunci, []).append(line_id)
                return
            pv = PemutarVideo(p, self.rasio)
            self._pemutar[kunci] = pv
            if self.model_path and (self.lacak_line == "*"
                                    or line_id in self.lacak_line):
                pl = Pelacak(line_id, pv, self.model_path,
                             fps_lacak=self.fps_lacak,
                             jeda_keluar=self.jeda_keluar,
                             min_geser=self.min_geser,
                             min_hadir=self.min_hadir,
                             min_conf=self.min_conf)
                pv.pelacak = pl
                self.pelacak[line_id] = pl
                pl.start()
            pv.start()
            self.video[line_id] = pv
        else:
            self.foto[line_id] = p

    def daftar(self):
        out = [(k, v.name, "foto") for k, v in self.foto.items()]
        out += [(k, v.path.name, "video") for k, v in self.video.items()]
        return sorted(out)

    def jpeg(self, line_id):
        """JPEG terbaru untuk line ini, atau None kalau tidak ada sumbernya."""
        if line_id in self.video:
            return self.video[line_id].jpeg() or None
        p = self.foto.get(line_id)
        if p is None:
            return None
        g = cv2.imread(str(p))                # dibaca ulang: boleh diganti
        if g is None:
            return None
        ok, buf = cv2.imencode(".jpg", ke_rasio(g, self.rasio),
                               [cv2.IMWRITE_JPEG_QUALITY, 92])
        return buf.tobytes() if ok else None

    def jpeg_ke(self, line_id):
        """(jpeg, nomor_frame). Foto diam nomornya selalu 0."""
        if line_id in self.video:
            return self.video[line_id].jpeg_ke()
        return self.jpeg(line_id), 0

    def fps(self, line_id):
        pv = self.video.get(line_id)
        return pv.fps if pv and pv.fps else 0.0

    def kejadian(self, line_id=None):
        out = []
        for lid, pl in self.pelacak.items():
            if line_id and lid != line_id:
                continue
            out += pl.kejadian
        return sorted(out, key=lambda k: k["masuk"], reverse=True)

    def hentikan(self):
        for pl in self.pelacak.values():
            pl.stop.set()
        for pv in self.video.values():
            pv.stop.set()


class Handler(BaseHTTPRequestHandler):
    katalog = None

    def log_message(self, *a):
        pass

    def _kirim(self, kode, tipe, isi):
        self.send_response(kode)
        self.send_header("Content-Type", tipe)
        self.send_header("Content-Length", str(len(isi)))
        # Dashboard membaca piksel dari canvas untuk pratinjau kalibrasi.
        # Tanpa header ini canvas jadi "tainted" dan pembacaannya mati.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(isi)

    def do_GET(self):
        u = urlparse(self.path)
        src = (parse_qs(u.query).get("src") or [""])[0]

        if u.path in ("/frame", "/frame.jpg", "/api/frame.jpeg"):
            buf = self.katalog.jpeg(src)
            if buf is None:
                self._kirim(404, "text/plain",
                            ("Tidak ada sumber untuk '%s'." % src).encode())
                return
            self._kirim(200, "image/jpeg", buf)

        elif u.path == "/kejadian":
            import json as _json
            baris = []
            for k in self.katalog.kejadian(src or None):
                baris.append({
                    "line_id": k["line_id"],
                    "masuk": datetime.fromtimestamp(k["masuk"]).strftime("%H.%M.%S"),
                    "keluar": (datetime.fromtimestamp(k["keluar"]).strftime("%H.%M.%S")
                               if k["keluar"] else None),
                    "durasi_detik": k["durasi"],
                })
            self._kirim(200, "application/json", _json.dumps(baris, indent=1).encode())

        elif u.path in ("/stream", "/stream.mjpg"):
            # MJPEG bersambung: <img src> menahan koneksi dan memperbarui
            # sendiri. Tanpa ini dashboard hanya bisa menarik satu gambar per
            # detik — app.js memakai Math.max(1, IMG_REFRESH), jadi 1 fps
            # adalah lantai keras di mode "img".
            if self.katalog.jpeg(src) is None:
                self._kirim(404, "text/plain",
                            ("Tidak ada sumber untuk '%s'." % src).encode())
                return
            self.send_response(200)
            self.send_header("Content-Type",
                             "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            fps = self.katalog.fps(src) or 10.0
            jeda = 1.0 / min(fps, 25.0)       # 25 fps sudah mulus di browser
            terakhir, kirim_t = -1, 0.0
            try:
                while True:
                    data, ke = self.katalog.jpeg_ke(src)
                    now = time.time()
                    # Frame baru -> kirim. Frame sama (foto diam) -> kirim ulang
                    # tiap 2 detik saja: cukup untuk menahan koneksi tetap
                    # hidup, tanpa membanjiri jaringan dengan gambar identik.
                    if data and (ke != terakhir or now - kirim_t > 2.0):
                        terakhir, kirim_t = ke, now
                        self.wfile.write(
                            b"--frame\r\nContent-Type: image/jpeg\r\n"
                            b"Content-Length: " + str(len(data)).encode() +
                            b"\r\n\r\n" + data + b"\r\n")
                    time.sleep(jeda)
            except (BrokenPipeError, ConnectionResetError):
                pass

        elif u.path == "/":
            baris = "".join(
                '<li><a href="/frame?src=%s">%s</a> &mdash; %s <i>(%s)</i></li>'
                % (lid, lid, nama, jenis)
                for lid, nama, jenis in self.katalog.daftar())
            self._kirim(200, "text/html; charset=utf-8",
                        ("<h3>Sumber kamera</h3><ul>%s</ul>"
                         % (baris or "<li>(folder kosong)</li>")).encode())
        else:
            self._kirim(404, "text/plain", b"tidak ada")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="ai/foto",
                    help="folder berisi <line_id>.jpg / <line_id>.mp4")
    ap.add_argument("--port", type=int, default=1984)
    ap.add_argument("--rasio", default="16:9",
                    help="samakan rasio dengan kotak kamera; 'off' untuk apa adanya")
    ap.add_argument("--map", action="append", default=[], metavar="LINE=BERKAS",
                    help="petakan line ke berkas tertentu, mis. ajl-02=vid2.mp4. "
                         "Boleh diulang. Untuk banyak line, tulis saja di "
                         "<dir>/peta.txt — dibaca otomatis")
    ap.add_argument("--lacak", nargs="?", const="*", default=None,
                    metavar="LINE,LINE",
                    help="deteksi & lacak operator, gambar kotak, catat "
                         "masuk/keluar. Tanpa nilai = semua video. Terukur di "
                         "mesin 8 inti: 8 video terlacak memakan 6,3 inti "
                         "SEKALIPUN deteksi diturunkan ke 2 fps — biayanya "
                         "per-line, bukan per-frame. Sebutkan line-nya saja "
                         "kalau tidak semua perlu: --lacak ajl-01,ajl-02")
    ap.add_argument("--model", default="yolov8s.pt",
                    help="yolov8s memisahkan orang dari mesin lewat confidence "
                         "(0.61-0.83 vs 0.43); yolov8n tidak bisa, conf-nya "
                         "tumpang tindih. 137 vs 40 ms/frame di CPU")
    ap.add_argument("--fps-lacak", type=float, default=6.0,
                    help="laju deteksi (bukan laju tampilan). yolov8s ~137 ms/"
                         "frame di CPU, jadi 6 fps sudah memakan ~0,8 inti per "
                         "line. Jangan disamakan dengan fps video")
    ap.add_argument("--min-geser", type=float, default=0.0,
                    help="piksel perpindahan minimum agar deteksi diakui ORANG. "
                         "0 = MATI, dan biarkan mati. Terukur di vid1: bagian "
                         "mesin bergeser 23.8 px, tiga operator yang berdiri "
                         "melayani mesin hanya 3.5/9.3/16 px — filter ini "
                         "membuang operator, bukan mesin")
    ap.add_argument("--jeda-keluar", type=float, default=5.0,
                    help="detik tanpa terdeteksi sebelum disebut KELUAR. "
                         "Menahan deteksi yang putus sesaat saat terhalang mesin")
    ap.add_argument("--min-conf", type=float, default=0.50,
                    help="ambang keyakinan deteksi. Di vid1 dengan yolov8s, "
                         "orang 0.61-0.83 dan salah-deteksi 0.43 — inilah "
                         "pemisah yang sebenarnya bekerja")
    ap.add_argument("--min-hadir", type=float, default=3.0,
                    help="detik minimum sebelum kehadiran DICATAT sebagai "
                         "kejadian. Lintasan sekejap (orang lewat di lorong, "
                         "kotak yang salah lolos saring) dibuang, tidak jadi "
                         "baris masuk/keluar 0 detik")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")

    rasio = None
    if a.rasio.lower() != "off":
        pj, pb = a.rasio.split(":")
        rasio = float(pj) / float(pb)

    peta = {}
    for m in a.map:
        if "=" not in m:
            raise SystemExit("--map harus berbentuk LINE=BERKAS, dapat: %s" % m)
        k, v = m.split("=", 1)
        peta[k.strip()] = v.strip()

    lacak_line = "*"
    if a.lacak and a.lacak != "*":
        lacak_line = {v.strip() for v in a.lacak.split(",") if v.strip()}
    if a.lacak:
        log.info("Model %s — satu salinan per line (%s)", a.model,
                 "semua video" if lacak_line == "*"
                 else ", ".join(sorted(lacak_line)))

    folder = Path(a.dir)
    folder.mkdir(parents=True, exist_ok=True)
    # peta.txt jadi dasar, --map di baris perintah menimpanya
    gabung = baca_peta(folder)
    gabung.update(peta)

    kat = Katalog(folder, rasio, gabung,
                  model_path=a.model if a.lacak else None,
                  fps_lacak=a.fps_lacak, jeda_keluar=a.jeda_keluar,
                  min_geser=a.min_geser, min_hadir=a.min_hadir,
                  min_conf=a.min_conf, lacak_line=lacak_line)

    isi = kat.daftar()
    if not isi:
        log.warning("Folder %s kosong. Simpan berkas dengan nama line_id.", folder)
    else:
        log.info("%d sumber:", len(isi))
        for lid, nama, jenis in isi:
            log.info("   %-10s <- %-16s (%s)", lid, nama, jenis)

    Handler.katalog = kat
    srv = ThreadingHTTPServer(("0.0.0.0", a.port), Handler)
    if rasio:
        log.info("Rasio dipadankan ke %s", a.rasio)
    log.info("Siap: http://127.0.0.1:%d/  (Ctrl+C untuk berhenti)", a.port)
    if kat.pelacak:
        log.info("Pelacakan AKTIF pada %d video (%.0f fps deteksi, "
                 "keluar setelah %.0f detik). Catatan: /kejadian",
                 len(kat.pelacak), a.fps_lacak, a.jeda_keluar)
    log.info("Mulus (MJPEG) : CCTV_STREAM_URL='http://127.0.0.1:%d/stream?src={id}'"
             "  + CCTV_STREAM_IMG_REFRESH=0", a.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log.info("berhenti")
    finally:
        kat.hentikan()


if __name__ == "__main__":
    main()
