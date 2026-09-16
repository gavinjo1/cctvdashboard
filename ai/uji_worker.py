#!/usr/bin/env python3
"""Uji aturan pengiriman alert di worker. Jalankan langsung:

    ./venv-ai/bin/python ai/uji_worker.py

Tidak butuh dashboard, kamera, atau model — requests.post diganti tiruan.
Ada karena bug di bawah ini pernah terjadi dan tidak terlihat sama sekali
dari luar: alert hilang diam-diam, log bersih, dashboard tampak sehat.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests                                      # noqa: E402
import worker as W                                   # noqa: E402


class PostTiruan:
    def __init__(self):
        self.n = 0
        self.gagal = False
        self.tolak = False

    def __call__(self, url, **kw):
        self.n += 1
        if self.gagal:
            raise requests.RequestException("jaringan putus")
        if self.tolak:
            return types.SimpleNamespace(ok=False, status_code=401, text="kunci salah")
        return types.SimpleNamespace(ok=True, status_code=200, text="")


def worker_kosong(cooldown=600):
    """CameraWorker tanpa __init__: tanpa kamera, model, atau jaringan."""
    w = W.CameraWorker.__new__(W.CameraWorker)
    w.cfg = {"cooldown_seconds": cooldown}
    w.line_id = "uji"
    w.dashboard = "http://x"
    w.headers = {}
    w.last_alert = {}
    return w


def periksa(nama, dapat, harap):
    tanda = "ok  " if dapat == harap else "GAGAL"
    print("  [%s] %-52s %s (harap %s)" % (tanda, nama, dapat, harap))
    return dapat == harap


def uji_gagal_kirim_tidak_menutup_cooldown():
    """Alert yang gagal terkirim HARUS dicoba lagi, bukan hilang 10 menit.

    Kalau cooldown dipasang sebelum POST, satu kegagalan jaringan membuang
    alert itu dan menutup label yang sama selama cooldown_seconds penuh.
    Mesin berhenti tanpa penanganan jadi tidak dilaporkan, tanpa jejak.
    """
    post = PostTiruan()
    W.requests.post = post
    w = worker_kosong()
    lolos = True

    post.gagal = True
    w.send_alert("Mesin stop", "a", 300, 0.9)
    lolos &= periksa("POST pertama gagal", post.n, 1)

    w.send_alert("Mesin stop", "a", 300, 0.9)
    lolos &= periksa("ulang segera: ditahan JEDA_ULANG_ALERT", post.n, 1)

    w.last_alert["Mesin stop"] -= W.JEDA_ULANG_ALERT + 1
    post.gagal = False
    w.send_alert("Mesin stop", "a", 300, 0.9)
    lolos &= periksa("lewat JEDA_ULANG_ALERT: dicoba lagi", post.n, 2)

    w.send_alert("Mesin stop", "a", 300, 0.9)
    lolos &= periksa("sudah berhasil: cooldown penuh berlaku", post.n, 2)
    return lolos


def uji_ditolak_tidak_diulang_terus():
    """Ditolak (kunci salah, isi salah) tidak boleh diulang tiap frame."""
    post = PostTiruan()
    W.requests.post = post
    w = worker_kosong()
    post.tolak = True

    w.send_alert("Mesin stop", "a", 300, 0.9)
    w.send_alert("Mesin stop", "a", 300, 0.9)
    return periksa("ditolak dashboard: cooldown penuh, tidak membanjiri log",
                   post.n, 1)


def uji_label_berbeda_tidak_saling_menutup():
    post = PostTiruan()
    W.requests.post = post
    w = worker_kosong()
    w.send_alert("Mesin stop", "a", 300, 0.9)
    w.send_alert("Operator tidak di area", "a", 300, 0.9)
    return periksa("label berbeda punya cooldown sendiri", post.n, 2)


def main():
    asli = requests.post
    uji = [uji_gagal_kirim_tidak_menutup_cooldown,
           uji_ditolak_tidak_diulang_terus,
           uji_label_berbeda_tidak_saling_menutup]
    hasil = []
    try:
        for f in uji:
            print("\n%s" % f.__name__)
            hasil.append(f())
    finally:
        W.requests.post = asli
    print("\n%d/%d lulus" % (sum(hasil), len(hasil)))
    return 0 if all(hasil) else 1


if __name__ == "__main__":
    sys.exit(main())
