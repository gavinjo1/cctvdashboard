#!/usr/bin/env python3
"""Uji pencatatan episode lampu + perbaikan operator.

    ./venv/bin/python app/uji_eventlog.py

Tanpa dashboard, kamera, atau database. Dua kasus di bawah pernah membuat
angka yang dipakai PPIC salah, dan keduanya tidak terlihat dari layar.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.eventlog import EpisodeTracker                      # noqa: E402

T0 = datetime(2026, 9, 17, 8, 30, 0)

# Dipanggil langsung dengan datetime, BUKAN lewat observe_many(): jalur itu
# menerjemahkan epoch dari worker dan menolak jam yang jauh dari sekarang,
# sehingga tanggal tetap di dalam tes akan dibuang. Yang diuji di sini adalah
# aturan episodenya, bukan penerjemahan jamnya.


def periksa(nama, dapat, harap):
    ok = dapat == harap
    print("  [%s] %-46s %s (harap %s)"
          % ("ok  " if ok else "GAGAL", nama, dapat, harap))
    return ok


def uji_operator_sudah_di_tempat():
    """Operator yang SUDAH berdiri di line saat lampu menyala.

    Kasus paling sering di pabrik. Dulu penjagaan `saat >= ep.started`
    membuangnya diam-diam: episodenya tercatat tanpa respons sama sekali,
    seolah tidak ada yang menangani mesin itu.
    """
    tutup = []
    t = EpisodeTracker(on_close=tutup.append)

    nyala = T0 + timedelta(seconds=20)          # 08:30:20 merah menyala
    padam = T0 + timedelta(seconds=75)          # 08:31:15 merah padam
    sudah_di_sana = T0 - timedelta(minutes=5)   # operator datang 08:25

    t.observe("ajl-01", 1, "stop", "merah", nyala)
    t.catat_operator("ajl-01", sudah_di_sana)
    t.observe("ajl-01", 1, "run", "", padam)

    if not tutup:
        return periksa("episode tercatat", 0, 1)
    ep = tutup[-1].row()
    lolos = periksa("durasi mesin mati", ep["duration_sec"], 55)
    lolos &= periksa("respons (operator sudah di tempat)", ep["respons_sec"], 0)
    lolos &= periksa("perbaikan", ep["perbaikan_sec"], 55)
    return lolos


def uji_worker_mati_di_tengah():
    """Worker berhenti saat lampu menyala, lalu hidup lagi berjam-jam kemudian.

    Episode tetap menganga. Tanpa penjagaan, paket pertama setelah worker
    hidup menutupnya dengan durasi sepanjang seluruh gangguan — "mesin
    berhenti 8 jam" yang tidak pernah terjadi, tepat di metrik yang diminta.
    """
    tutup = []
    t = EpisodeTracker(on_close=tutup.append, batas_senjang=120)

    nyala = T0 + timedelta(seconds=20)
    t.observe("ajl-01", 1, "stop", "merah", nyala)
    # masih menyala 30 detik kemudian — konfirmasi terakhir
    t.observe("ajl-01", 1, "stop", "merah", nyala + timedelta(seconds=30))
    # worker mati. Hidup lagi 8 jam kemudian, lampu sudah padam.
    t.observe("ajl-01", 1, "run", "", T0 + timedelta(hours=8))

    if not tutup:
        return periksa("episode tercatat", 0, 1)
    ep = tutup[-1]
    lolos = periksa("durasi TIDAK ikut gangguan", ep.row()["duration_sec"], 30)
    lolos &= periksa("ditandai terpotong", ep.terpotong, True)
    return lolos


def main():
    uji = [uji_operator_sudah_di_tempat, uji_worker_mati_di_tengah]
    hasil = []
    for f in uji:
        print("\n%s" % f.__name__)
        hasil.append(f())
    print("\n%d/%d lulus" % (sum(hasil), len(hasil)))
    return 0 if all(hasil) else 1


if __name__ == "__main__":
    sys.exit(main())
