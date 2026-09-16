#!/usr/bin/env python3
"""Uji pembacaan WARNA lampu terhadap rekaman pabrik yang warnanya diketahui.

    ./venv-ai/bin/python ai/uji_warna_lampu.py

Warna adalah PEMERIKSA SILANG, bukan pengganti posisi. Arti tetap datang
dari posisi segmen; warna dipakai untuk menangkap kotak menara yang meleset
— kesalahan yang paling sering terjadi dan paling sulit terlihat, karena
angkanya tetap masuk akal.

Kasus di bawah dipilih karena pernah MEMATAHKAN implementasi sebelumnya:
lampu merah di vid3.jpeg intinya jenuh sampai putih (median S seluruh kotak
cuma 12), dan cara lama yang mengambil piksel paling terang membacanya
sebagai "putih".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2                                           # noqa: E402
import numpy as np                                   # noqa: E402
from lamp import warna_segmen                        # noqa: E402

FOTO = Path(__file__).resolve().parent / "foto"

#: (nama, berkas, detik, kotak piksel y0,y1,x0,x1, warna yang benar)
KASUS = [
    ("merah — inti jenuh jadi putih", "vid3.jpeg", 0.0,
     (525, 600, 545, 615), "merah"),
    ("hijau", "vid16.mp4", 0.0, (357, 366, 232, 250), "hijau"),
    ("putih", "vid16.mp4", 0.0, (366, 376, 232, 250), "putih"),
]


def ambil(berkas, detik):
    cap = cv2.VideoCapture(str(FOTO / berkas))
    if not cap.isOpened():
        return None
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    f = None
    for _ in range(max(1, int(detik * fps)) + 1):
        ok, bingkai = cap.read()
        if not ok:
            break                   # JANGAN menimpa f: pada BERKAS GAMBAR
        f = bingkai                 # pembacaan kedua gagal dan frame pertama
    cap.release()                   # yang sudah benar ikut hilang
    return f


def main():
    hasil = []
    for nama, berkas, detik, (y0, y1, x0, x1), benar in KASUS:
        f = ambil(berkas, detik)
        if f is None:
            print("  [LEWAT] %s — %s tidak terbaca" % (nama, berkas))
            continue
        hsv = cv2.cvtColor(f[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
        v90 = float(np.percentile(hsv[:, :, 2], 90))
        warna, yakin = warna_segmen(hsv, v90)
        ok = warna == benar
        hasil.append(ok)
        print("  [%s] %-34s -> %-6s (yakin %.2f, benar %s)"
              % ("ok  " if ok else "GAGAL", nama, warna, yakin, benar))

    if not hasil:
        print("\nTidak ada kasus yang bisa diuji — rekaman tidak ada.")
        return 0
    print("\n%d/%d lulus" % (sum(hasil), len(hasil)))
    return 0 if all(hasil) else 1


if __name__ == "__main__":
    sys.exit(main())
