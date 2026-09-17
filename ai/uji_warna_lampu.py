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
from lamp import PembacaMenara, read_tower, warna_segmen   # noqa: E402

FOTO = Path(__file__).resolve().parent / "foto"

#: (nama, berkas, detik, kotak piksel y0,y1,x0,x1, warna yang benar)
KASUS = [
    ("merah — inti jenuh jadi putih", "vid3.jpeg", 0.0,
     (525, 600, 545, 615), "merah"),
    ("hijau", "vid8.mp4", 0.0, (357, 366, 232, 250), "hijau"),
    ("putih", "vid8.mp4", 0.0, (366, 376, 232, 250), "putih"),
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


def uji_kedip():
    """Lampu BERKEDIP tidak boleh menghapus warna atau mengacak jam transisi.

    Warna diukur dari piksel yang sedang terang. Lampu berkedip gelap
    separuh waktu, jadi kalau warna hanya diambil saat terang, ia bolak-balik
    "hijau" <-> None beberapa kali per detik. Itu me-reset status_sejak terus
    dan melahirkan episode baru hampir tiap paket — tepat pada keadaan SETUP,
    yang memang ditandai kedipan. Terukur sebelum diperbaiki: 17 reset dan 67
    frame kehilangan warna dalam 6 detik.
    """
    f = ambil("vid8.mp4", 0.0)
    if f is None:
        print("  [LEWAT] kedip — vid8.mp4 tidak terbaca")
        return None
    gelap = (f.astype(np.float32) * 0.25).astype(np.uint8)

    p = PembacaMenara(1, [48.54, 40.59, 3.77, 4.82],
                      ["merah", "hijau", "putih", "kuning"])
    p.rekam_baseline(gelap)

    reset, warna_hilang, sejak_lama = 0, 0, None
    for k in range(150):                        # 6 detik @ 25 fps
        t = k * 0.04
        hasil, _ = read_tower(f if int(t * 3) % 2 == 0 else gelap, [p], now=t)
        r = hasil[0]
        if sejak_lama is not None and r["sejak"] != sejak_lama:
            reset += 1
        sejak_lama = r["sejak"]
        if r["status"] != "run" and not r["warna"]:
            warna_hilang += 1

    ok = reset <= 2 and warna_hilang == 0
    print("  [%s] %-34s -> %d reset jam, %d frame tanpa warna"
          % ("ok  " if ok else "GAGAL", "kedip tidak mengacak jam transisi",
             reset, warna_hilang))
    return ok


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

    k = uji_kedip()
    if k is not None:
        hasil.append(k)

    if not hasil:
        print("\nTidak ada kasus yang bisa diuji — rekaman tidak ada.")
        return 0
    print("\n%d/%d lulus" % (sum(hasil), len(hasil)))
    return 0 if all(hasil) else 1


if __name__ == "__main__":
    sys.exit(main())
