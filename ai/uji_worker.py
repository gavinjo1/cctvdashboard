#!/usr/bin/env python3
"""Uji pengiriman alert (ai/pengirim.py) dan aturannya (ai/aturan.py).

    ./venv-ai/bin/python ai/uji_worker.py

Tanpa dashboard, kamera, atau model. Setelah worker dipecah, keduanya bisa
diuji terpisah: aturan sama sekali tidak menyentuh jaringan, dan pengirim
tidak tahu apa-apa soal aturan.

Kasus di bawah pernah terjadi dan tidak terlihat dari luar: alert hilang
diam-diam, log bersih, dashboard tampak sehat.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests                                      # noqa: E402
from aturan import AturanAlert                       # noqa: E402
from pengirim import JEDA_ULANG_ALERT, Pengirim      # noqa: E402


class PostTiruan:
    """Pengganti Session.post — hitung panggilan, bisa dibuat gagal/menolak."""

    def __init__(self):
        self.n = 0
        self.gagal = False
        self.tolak = False

    def __call__(self, url, **kw):
        self.n += 1
        if self.gagal:
            raise requests.RequestException("jaringan putus")
        if self.tolak:
            return types.SimpleNamespace(ok=False, status_code=401,
                                         text="kunci salah")
        return types.SimpleNamespace(ok=True, status_code=200, text="")


def pengirim_uji(cooldown=600):
    """Pengirim yang tidak pernah menyentuh jaringan."""
    p = Pengirim("http://x", "uji", cooldown=cooldown)
    p.http = types.SimpleNamespace(post=PostTiruan(), get=None)
    return p


def periksa(nama, dapat, harap):
    ok = dapat == harap
    print("  [%s] %-52s %s (harap %s)"
          % ("ok  " if ok else "GAGAL", nama, dapat, harap))
    return ok


# ---------------------------------------------------------------- pengirim
def uji_gagal_kirim_tidak_menutup_cooldown():
    """Alert yang gagal terkirim HARUS dicoba lagi, bukan hilang 10 menit.

    Kalau cooldown dipasang sebelum POST, satu kegagalan jaringan membuang
    alert itu dan menutup label yang sama selama cooldown_seconds penuh.
    Mesin berhenti tanpa penanganan jadi tidak dilaporkan, tanpa jejak.
    """
    p = pengirim_uji()
    post = p.http.post
    lolos = True

    post.gagal = True
    p.kirim_alert("Mesin stop", "a", 300, 0.9)
    lolos &= periksa("POST pertama gagal", post.n, 1)

    p.kirim_alert("Mesin stop", "a", 300, 0.9)
    lolos &= periksa("ulang segera: ditahan JEDA_ULANG_ALERT", post.n, 1)

    p.last_alert["Mesin stop"] -= JEDA_ULANG_ALERT + 1
    post.gagal = False
    p.kirim_alert("Mesin stop", "a", 300, 0.9)
    lolos &= periksa("lewat JEDA_ULANG_ALERT: dicoba lagi", post.n, 2)

    p.kirim_alert("Mesin stop", "a", 300, 0.9)
    lolos &= periksa("sudah berhasil: cooldown penuh berlaku", post.n, 2)
    return lolos


def uji_ditolak_tidak_diulang_terus():
    """Ditolak (kunci salah, isi salah) tidak boleh diulang tiap frame."""
    p = pengirim_uji()
    p.http.post.tolak = True
    p.kirim_alert("Mesin stop", "a", 300, 0.9)
    p.kirim_alert("Mesin stop", "a", 300, 0.9)
    return periksa("ditolak dashboard: cooldown penuh, tidak membanjiri log",
                   p.http.post.n, 1)


def uji_label_berbeda_tidak_saling_menutup():
    p = pengirim_uji()
    p.kirim_alert("Mesin stop", "a", 300, 0.9)
    p.kirim_alert("Operator tidak di area", "a", 300, 0.9)
    return periksa("label berbeda punya cooldown sendiri", p.http.post.n, 2)


# ---------------------------------------------------------------- aturan
CFG = {"absent_seconds": 120, "crowd_min": 3, "crowd_seconds": 30,
       "response_seconds": 300}


def label(alerts):
    return sorted(a.label for a in alerts)


def uji_aturan_operator_hilang():
    """Jam palsu: "operator hilang 3 menit" diperiksa dalam milidetik.

    Inilah gunanya aturan dipisah dari jaringan — dulu menguji ini berarti
    menjalankan worker sungguhan dan menunggu dua menit.
    """
    a = AturanAlert(CFG, 1000.0)
    lolos = periksa("baru mulai, belum ada alert",
                    label(a.nilai(0, 0, 1000.0)), [])
    lolos &= periksa("119 detik: masih diam",
                     label(a.nilai(0, 0, 1119.0)), [])
    lolos &= periksa("121 detik: operator tidak di area",
                     label(a.nilai(0, 0, 1121.0)), ["Operator tidak di area"])
    a.nilai(1, 0, 1122.0)          # operator kembali
    lolos &= periksa("operator kembali: penghitung direset",
                     label(a.nilai(0, 0, 1200.0)), [])
    return lolos


def uji_aturan_zona_belum_dikalibrasi():
    """Zona kosong = jumlah orang selalu nol. Aturan orang HARUS dimatikan.

    Tanpa penjagaan ini, tiap kamera yang belum dikalibrasi membanjiri
    dashboard dengan "Operator tidak di area" palsu — dan alert palsu yang
    banyak membuat operator berhenti mempercayai semua alert.
    """
    a = AturanAlert(CFG, 1000.0)
    return periksa("zona belum dikalibrasi: tidak ada alert orang",
                   label(a.nilai(0, 0, 9999.0, zone_ok=False)), [])


def uji_aturan_mesin_stop():
    """Mesin stop lama TAPI ada operator di area = bukan alert."""
    a = AturanAlert(CFG, 1000.0)
    a.nilai(0, 1, 1000.0)
    lolos = periksa("stop 301 detik, tidak ada operator",
                    "Mesin stop tanpa penanganan" in
                    label(a.nilai(0, 1, 1301.0)), True)

    b = AturanAlert(CFG, 1000.0)
    b.nilai(1, 1, 1000.0)
    lolos &= periksa("stop 301 detik TAPI operator ada",
                     "Mesin stop tanpa penanganan" in
                     label(b.nilai(1, 1, 1301.0)), False)
    return lolos


def main():
    uji = [uji_gagal_kirim_tidak_menutup_cooldown,
           uji_ditolak_tidak_diulang_terus,
           uji_label_berbeda_tidak_saling_menutup,
           uji_aturan_operator_hilang,
           uji_aturan_zona_belum_dikalibrasi,
           uji_aturan_mesin_stop]
    hasil = []
    for f in uji:
        print("\n%s" % f.__name__)
        hasil.append(f())
    print("\n%d/%d lulus" % (sum(hasil), len(hasil)))
    return 0 if all(hasil) else 1


if __name__ == "__main__":
    sys.exit(main())
