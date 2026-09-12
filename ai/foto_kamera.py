#!/usr/bin/env python3
"""Sajikan FOTO DIAM sebagai kamera, satu foto per line.

Untuk menyetel kotak lampu tanpa kamera dan tanpa video: cukup satu foto
menara per line. Gambarnya diam, jadi kotak bisa disetel sedetail mungkin
tanpa dikejar gambar yang berubah.

    ai/foto/ajl-01.jpg   ->  http://127.0.0.1:1984/frame?src=ajl-01
    ai/foto/ajl-02.jpg   ->  http://127.0.0.1:1984/frame?src=ajl-02

Jalankan:

    python ai/foto_kamera.py --dir ai/foto

Lalu dashboard, di terminal lain:

    CCTV_STREAM_MODE=img \\
    CCTV_STREAM_URL="http://127.0.0.1:1984/frame?src={id}" \\
    python run.py

Line yang tidak punya foto tetap tampil kosong — tidak mengganggu yang lain.

Berkas dibaca ULANG setiap permintaan, jadi foto boleh diganti tanpa
menghentikan server: timpa berkasnya, muat ulang halaman, selesai.
"""
import argparse
import logging
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import cv2
import numpy as np

log = logging.getLogger("foto")

EKSTENSI = (".jpg", ".jpeg", ".png", ".webp", ".bmp")


def ke_rasio(gambar, rasio=16 / 9.0):
    """Beri bilah gelap agar foto berasio sama dengan kotak kamera.

    KENAPA PERLU: kanvas kalibrasi seluas KOTAK kamera (16:9), sedangkan
    pembacaan ROI memetakan persen ke FOTO ASLINYA. Kalau rasionya beda,
    CSS object-fit menambahkan bilah kosong — dan kotak yang digambar di
    lampu akan dibaca dari tempat lain. Foto potret paling parah.

    Dengan dipadankan di sini, yang digambar dan yang dibaca memakai
    kerangka yang sama, tanpa menyentuh kode kalibrasi.
    """
    h, w = gambar.shape[:2]
    if abs((w / float(h)) - rasio) < 0.01:
        return gambar
    if w / float(h) < rasio:                 # terlalu tinggi -> lebarkan
        baru_w, baru_h = int(round(h * rasio)), h
    else:                                     # terlalu lebar -> tinggikan
        baru_w, baru_h = w, int(round(w / rasio))
    kanvas = np.full((baru_h, baru_w, 3), 12, np.uint8)
    x, y = (baru_w - w) // 2, (baru_h - h) // 2
    kanvas[y:y + h, x:x + w] = gambar
    return kanvas


class Katalog:
    """Peta line_id -> berkas foto. Dipindai ulang saat berkas berubah."""

    def __init__(self, folder: Path):
        self.folder = folder
        self._cache = {}

    def berkas(self, line_id):
        """Cari berkas untuk line ini. None kalau tidak ada."""
        if not line_id:
            return None
        # nama berkas = line_id + ekstensi apa saja
        for ext in EKSTENSI:
            p = self.folder / (line_id + ext)
            if p.exists():
                return p
        return None

    def daftar(self):
        out = []
        if self.folder.is_dir():
            for p in sorted(self.folder.iterdir()):
                if p.suffix.lower() in EKSTENSI:
                    out.append((p.stem, p))
        return out


class Handler(BaseHTTPRequestHandler):
    katalog = None

    def log_message(self, *a):
        pass

    def _kirim(self, kode, tipe, isi):
        self.send_response(kode)
        self.send_header("Content-Type", tipe)
        self.send_header("Content-Length", str(len(isi)))
        # Dashboard membaca piksel dari canvas untuk pratinjau kalibrasi.
        # Tanpa header ini canvas jadi "tainted" dan pembacaan warna mati
        # dengan pesan yang terlihat seperti kerusakan lain.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(isi)

    def do_GET(self):
        u = urlparse(self.path)
        src = (parse_qs(u.query).get("src") or [""])[0]

        if u.path in ("/frame", "/frame.jpg", "/api/frame.jpeg"):
            p = self.katalog.berkas(src)
            if p is None:
                self._kirim(404, "text/plain",
                            ("Tidak ada foto untuk '%s'. Simpan sebagai %s/%s.jpg"
                             % (src, self.katalog.folder, src)).encode())
                return
            # dibaca ulang tiap permintaan: foto boleh diganti tanpa restart
            g = cv2.imread(str(p))
            if g is None:
                self._kirim(500, "text/plain",
                            ("Gagal membaca %s" % p.name).encode())
                return
            if self.server.rasio:
                g = ke_rasio(g, self.server.rasio)
            ok, buf = cv2.imencode(".jpg", g, [cv2.IMWRITE_JPEG_QUALITY, 92])
            if not ok:
                self._kirim(500, "text/plain", b"gagal encode")
                return
            self._kirim(200, "image/jpeg", buf.tobytes())

        elif u.path == "/":
            baris = "".join(
                '<li><a href="/frame?src=%s">%s</a> &mdash; %s</li>' % (n, n, p.name)
                for n, p in self.katalog.daftar())
            self._kirim(200, "text/html; charset=utf-8",
                        ("<h3>Foto kamera</h3><ul>%s</ul>"
                         "<p>Pakai di dashboard: "
                         "<code>CCTV_STREAM_URL=http://127.0.0.1:%d/frame?src={id}</code></p>"
                         % (baris or "<li>(folder kosong)</li>",
                            self.server.server_address[1])).encode())
        else:
            self._kirim(404, "text/plain", b"tidak ada")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="ai/foto",
                    help="folder berisi <line_id>.jpg")
    ap.add_argument("--port", type=int, default=1984)
    ap.add_argument("--rasio", default="16:9",
                    help="samakan rasio foto dengan kotak kamera; "
                         "'off' untuk menyajikan apa adanya")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
    folder = Path(a.dir)
    folder.mkdir(parents=True, exist_ok=True)
    kat = Katalog(folder)

    isi = kat.daftar()
    if not isi:
        log.warning("Folder %s masih kosong.", folder)
        log.warning("Simpan foto dengan nama line_id, mis. %s/ajl-01.jpg", folder)
    else:
        log.info("%d foto ditemukan:", len(isi))
        for nama, p in isi:
            log.info("   %-10s <- %s", nama, p.name)

    Handler.katalog = kat
    srv = ThreadingHTTPServer(("0.0.0.0", a.port), Handler)
    if a.rasio.lower() == "off":
        srv.rasio = None
        log.warning("Rasio TIDAK disamakan — kotak kalibrasi bisa meleset "
                    "kalau foto bukan 16:9")
    else:
        pj, pb = a.rasio.split(":")
        srv.rasio = float(pj) / float(pb)
        log.info("Foto dipadankan ke rasio %s (bilah gelap bila perlu)", a.rasio)
    log.info("Siap: http://127.0.0.1:%d/  (Ctrl+C untuk berhenti)", a.port)
    log.info("Dashboard: CCTV_STREAM_MODE=img "
             "CCTV_STREAM_URL='http://127.0.0.1:%d/frame?src={id}'", a.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log.info("berhenti")


if __name__ == "__main__":
    main()
