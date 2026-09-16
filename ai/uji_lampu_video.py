#!/usr/bin/env python3
"""Uji pembacaan menara lampu terhadap REKAMAN VIDEO, tanpa kamera.

Dipakai untuk dua hal:
  1. Menyetel ambang dari rekaman pabrik, di meja, tanpa berdiri di lantai
     produksi. Sekali rekam, berkali-kali coba.
  2. Membuktikan pembacaan benar sebelum dipasang ke worker.

CARA PAKAI — dua langkah
------------------------
1) Cari kotak menaranya. Simpan satu frame dengan kisi persen:

       python ai/uji_lampu_video.py rekaman.mp4 --kisi 8

   Buka gambar yang dihasilkan, baca kotak menara dalam PERSEN
   (x kiri, y atas, lebar, tinggi). Kotak harus meliputi HANYA tumpukan
   mika — tanpa tutup atas, tanpa kaki hitam di bawahnya.

2) Jalankan pembacaan:

       python ai/uji_lampu_video.py rekaman.mp4 \\
           --tower 29,3,5,14 \\
           --indikator putih,merah,kuning,hijau \\
           --mulai 0 --sampai 17

   Tiap detik dicetak keadaan tiap segmen berikut angka ukurnya. Kolom
   `margin` adalah jarak ke ambang: lampu menyala harus jelas POSITIF,
   lampu padam jelas NEGATIF. Kalau keduanya berdempet di sekitar nol,
   kotaknya meleset — perbaiki kotak dulu, jangan ambangnya.

   Tambahkan --keluar-video hasil.mp4 untuk menyimpan rekaman beranotasi
   (harus berkas BARU, bukan video masukan).

CATATAN soal rekaman dengan kamera BERGERAK
-------------------------------------------
Kotak menara bersifat tetap terhadap frame. Kalau kamera bergeser atau
gambarnya berganti adegan, kotak yang tadinya pas akan meleset dan
pembacaannya jadi omong kosong. Karena itu batasi rentang uji dengan
--mulai/--sampai ke bagian yang kameranya diam.
"""
import argparse
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lamp import PembacaMenara, KEDIP, NYALA, PADAM   # noqa: E402


def simpan_kisi(video, detik, langkah, keluar):
    """Simpan satu frame dengan kisi persen, untuk membaca kotak menara."""
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    # baca berurutan: seek pada MOV sering meleset ke keyframe terdekat
    target = int(detik * fps)
    f = None
    for i in range(target + 1):
        ok, f = cap.read()
        if not ok:
            break
    cap.release()
    if f is None:
        sys.exit("Tidak bisa membaca frame pada detik %s" % detik)

    h, w = f.shape[:2]
    for p in range(0, 101, langkah):
        x, y = int(p / 100 * w), int(p / 100 * h)
        cv2.line(f, (x, 0), (x, h), (0, 255, 255), 1)
        cv2.line(f, (0, y), (w, y), (0, 255, 255), 1)
        cv2.putText(f, str(p), (x + 3, 18), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, (0, 255, 255), 1, cv2.LINE_AA)
        cv2.putText(f, str(p), (3, y - 3), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, (0, 255, 255), 1, cv2.LINE_AA)
    cv2.imwrite(keluar, f)
    print("Kisi persen disimpan: %s  (%dx%d, detik %s)" % (keluar, w, h, detik))
    print("Baca kotak menara dalam persen, lalu jalankan lagi dengan --tower")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--kisi", type=int, metavar="LANGKAH",
                    help="simpan frame dengan kisi persen lalu keluar")
    ap.add_argument("--kisi-detik", type=float, default=1.0,
                    help="detik ke berapa frame kisi diambil")
    ap.add_argument("--tower", help="kotak menara, persen: x,y,lebar,tinggi")
    ap.add_argument("--indikator", default="putih,merah,kuning,hijau",
                    help="nama segmen ATAS->BAWAH, dipisah koma")
    ap.add_argument("--mulai", type=float, default=0.0, help="detik awal")
    ap.add_argument("--sampai", type=float, default=0.0, help="detik akhir (0=habis)")
    ap.add_argument("--baseline-detik", type=float, default=None,
                    help="detik saat menara PADAM, untuk merekam baseline")
    ap.add_argument("--delta-tetangga", type=float, default=40.0)
    ap.add_argument("--delta-baseline", type=float, default=45.0)
    ap.add_argument("--v-minimum", type=float, default=70.0)
    # dest DIBEDAKAN dari positional `video`. Kalau sama, keduanya menulis
    # ke a.video dan alat ini akan menimpa rekaman masukannya sendiri.
    ap.add_argument("--keluar-video", dest="keluar_video", metavar="BERKAS",
                    help="simpan rekaman beranotasi ke berkas BARU")
    ap.add_argument("--overlay", metavar="BERKAS", default="overlay.png",
                    help="simpan gambar batas segmen untuk DIPERIKSA MATA. "
                         "Ini langkah yang paling sering dilewati dan paling "
                         "sering jadi sumber kesalahan")
    a = ap.parse_args()

    if a.kisi:
        simpan_kisi(a.video, a.kisi_detik, a.kisi, "kisi.jpg")
        return
    if not a.tower:
        sys.exit("Butuh --tower. Jalankan dengan --kisi 8 dulu untuk mencarinya.")

    tower = [float(v) for v in a.tower.split(",")]
    indikator = [s.strip() for s in a.indikator.split(",") if s.strip()]
    if len(tower) != 4:
        sys.exit("--tower harus 4 angka: x,y,lebar,tinggi (persen)")

    cap = cv2.VideoCapture(a.video)
    if not cap.isOpened():
        sys.exit("Tidak bisa membuka: %s" % a.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    pemb = PembacaMenara(1, tower, indikator,
                         delta_baseline=a.delta_baseline,
                         delta_tetangga=a.delta_tetangga,
                         v_minimum=a.v_minimum)

    tulis = None
    if a.keluar_video:
        # Penjagaan terakhir: menulis ke berkas masukan akan merusaknya.
        if Path(a.keluar_video).resolve() == Path(a.video).resolve():
            sys.exit("--keluar-video tidak boleh sama dengan video masukan.")
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        tulis = cv2.VideoWriter(a.keluar_video, cv2.VideoWriter_fourcc(*"mp4v"),
                                fps, (w, h))

    # Overlay dulu, SEBELUM angka apa pun dipercaya. Kotak yang meleset
    # menghasilkan angka yang salah tetapi tetap terlihat masuk akal —
    # satu-satunya cara menangkapnya adalah melihat batasnya di gambar.
    ok0, f0 = cap.read()
    if ok0:
        vis = f0.copy()
        H0, W0 = vis.shape[:2]
        tp = (int(tower[0]/100*W0), int(tower[1]/100*H0),
              int(tower[2]/100*W0), int(tower[3]/100*H0))
        seg = tp[3] / float(len(indikator))
        cv2.rectangle(vis, (tp[0], tp[1]), (tp[0]+tp[2], tp[1]+tp[3]), (255,0,255), 2)
        for k, nama in enumerate(indikator):
            yk = int(tp[1] + k*seg)
            cv2.line(vis, (tp[0]-14, yk), (tp[0]+tp[2]+14, yk), (0,255,255), 1)
            cv2.putText(vis, nama, (tp[0]+tp[2]+18, yk+16),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0,255,255), 1, cv2.LINE_AA)
        cv2.line(vis, (tp[0]-14, tp[1]+tp[3]), (tp[0]+tp[2]+14, tp[1]+tp[3]), (255,0,255), 2)
        if max(vis.shape[:2]) < 700:
            vis = cv2.resize(vis, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
        cv2.imwrite(a.overlay, vis)
        print("OVERLAY: %s  <-- PERIKSA INI DULU sebelum percaya angkanya." % a.overlay)
        print("         Tiap garis kuning harus jatuh tepat di batas mika.")
        print()
        # Buka ulang, jangan seek: pada BERKAS GAMBAR, seek ke frame 0 gagal
        # dan pembacaan utama jadi kosong tanpa pesan kesalahan apa pun.
        cap.release()
        cap = cv2.VideoCapture(a.video)

    print("menara %s | indikator (atas->bawah): %s | %.0f fps"
          % (tower, ", ".join(indikator), fps))
    print()
    lebar_ind = max(len(i) for i in indikator)
    print("  detik | " + " | ".join(i.ljust(lebar_ind + 8) for i in indikator))
    print("  " + "-" * (8 + len(indikator) * (lebar_ind + 11)))

    WARNA = {KEDIP: (60, 200, 240), NYALA: (80, 220, 90), PADAM: (130, 130, 130)}
    i, cetak_terakhir = 0, -1
    sampai = a.sampai or 1e9
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = i / fps
        i += 1
        if t < a.mulai:
            continue
        if t > sampai:
            break

        if a.baseline_detik is not None and pemb.baseline is None \
                and t >= a.baseline_detik:
            pemb.rekam_baseline(frame)
            print("  [baseline direkam pada %.1fs: %s]"
                  % (t, [round(b) for b in pemb.baseline]))

        bacaan = pemb.baca(frame, now=t)

        if int(t) != cetak_terakhir:
            cetak_terakhir = int(t)
            sel = []
            for b in bacaan:
                tanda = {KEDIP: "~", NYALA: "*", PADAM: " "}[b["keadaan"]]
                sel.append(("%s%-6s v%3.0f m%+4.0f"
                            % (tanda, b["keadaan"], b["v90"], b["margin"]))
                           .ljust(lebar_ind + 8))
            print("  %5.0f | %s" % (t, " | ".join(sel)))

        if tulis is not None:
            for b in bacaan:
                x, y, w_, h_ = b["roi"]
                col = WARNA[b["keadaan"]]
                cv2.rectangle(frame, (x, y), (x + w_, y + h_), col, 2)
                cv2.putText(frame, "%s %.0f" % (b["indikator"][:8], b["v90"]),
                            (x + w_ + 5, y + int(h_ * 0.7)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2, cv2.LINE_AA)
            tulis.write(frame)

    cap.release()
    if tulis is not None:
        tulis.release()
        print("\nRekaman beranotasi: %s" % a.keluar_video)
    print("\n  tanda:  * menyala tetap   ~ berkedip   (kosong) padam")
    print("  margin: jarak ke ambang. Menyala harus jelas POSITIF,")
    print("          padam jelas NEGATIF. Berdempet di nol = kotak meleset.")


if __name__ == "__main__":
    main()
