"""Pembacaan menara lampu mesin — ARTI DARI POSISI, SINYAL DARI KEDIP.

SUSUNAN MENARA (isi sesuai mesin — lihat zones.json)
----------------------------------------------------
Arti tiap segmen datang dari POSISI-nya, dan dipetakan lewat `indikator`
saat kalibrasi. Modul ini tidak tahu dan tidak perlu tahu warnanya.

BELUM DIPASTIKAN — WAJIB DICEK DI PABRIK
----------------------------------------
Apa yang terjadi saat mesin berproduksi normal? Ada dua kemungkinan, dan
keduanya menghasilkan status yang BERKEBALIKAN:

    saat_gelap="run"  menara hanya menyala kalau ada sesuatu; hijau cuma
                      berkedip saat setup lalu padam.
                      SEMUA PADAM = MESIN JALAN.

    saat_gelap="off"  hijau menyala terus selama produksi.
                      SEMUA PADAM = mesin mati atau lampu putus.

Salah memilih membuat status setiap mesin terbalik, dan hasilnya tetap
terlihat masuk akal di layar sehingga tidak akan ketahuan dari data saja.
Pastikan dengan melihat satu mesin yang sedang berproduksi normal.

SINYALNYA KEDIP, BUKAN NYALA TETAP. Karena itu laju sampling menjadi
   penting: pada 3 fps, kedip 1 Hz terbaca sebagai nyala-padam acak dan
   catatan kejadian penuh sampah. Modul ini murah (numpy pada beberapa
   potongan kecil, tanpa GPU), jadi jalankan pada 25-30 fps meskipun
   deteksi orang berjalan jauh lebih lambat.

ARTI DITENTUKAN POSISI, BUKAN WARNA
-----------------------------------
Modul ini tidak pernah memutuskan "ini merah atau kuning". Ia hanya menjawab
"segmen ke-berapa yang menyala", lalu nama indikatornya diambil dari urutan
yang diisi saat kalibrasi. Jauh lebih tahan terhadap cahaya pabrik yang
berubah, mika lampu yang pudar, dan white balance kamera yang bergeser.

MENILAI "MENYALA"
-----------------
Dipakai persentil ke-90 kanal V, bukan rata-rata: lampu LED yang menyala
sering hanya terang di inti kecilnya (sensor jenuh sehingga tampak keputihan),
dan rata-rata akan menenggelamkannya.

Dua acuan dipakai bersama:
  * acuan diri     — kecerahan segmen ini saat menara padam (direkam sekali)
  * acuan tetangga — segmen lain di menara yang sama menerima cahaya ruangan
                     yang sama; kalau satu jauh lebih terang, itu menyala

Acuan tetangga menyesuaikan diri terhadap perubahan cahaya, dan paling bersih
bila keadaan normalnya semua segmen gelap.
"""
import time
from collections import deque

import cv2
import numpy as np

# ---------------------------------------------------------------- ambang
#: selisih V90 terhadap baseline agar dianggap menyala
DELTA_BASELINE = 45.0
#: selisih V90 terhadap median segmen tetangga agar dianggap menyala
DELTA_TETANGGA = 40.0
#: kecerahan minimum mutlak — menahan false positive saat ruangan gelap total
V_MINIMUM = 70.0

# ---------------------------------------------------------------- kedip
#: panjang riwayat untuk menilai kedip (detik). Harus mencakup >= 2 siklus
#: kedip terlambat yang mungkin dipakai mesin.
JENDELA_KEDIP = 3.0
#: minimal pergantian nyala/padam dalam jendela agar disebut berkedip
MIN_TRANSISI = 2
#: kalau sekian detik terakhir SEMUANYA gelap, langsung sebut padam tanpa
#: menunggu jendela penuh. Tanpa ini, "setup selesai" baru terdeteksi
#: JENDELA_KEDIP detik setelah lampunya benar-benar mati.
GELAP_CEPAT = 1.0

#: keadaan segmen
PADAM, NYALA, KEDIP = "padam", "nyala", "kedip"

#: warna gambar overlay (BGR)
DRAW = {KEDIP: (60, 200, 240), NYALA: (80, 220, 90), PADAM: (130, 130, 130)}


def rect_from_percent(rect, w, h):
    """[x, y, w, h] dalam persen -> piksel."""
    x, y, rw, rh = rect
    return (int(x / 100 * w), int(y / 100 * h),
            max(1, int(rw / 100 * w)), max(1, int(rh / 100 * h)))


def bagi_segmen(tower_px, n):
    """Bagi kotak menara jadi n segmen sama tinggi, URUT DARI ATAS.

    Indeks 0 = paling atas, dipasangkan dengan indikator[0].

    PENTING saat kalibrasi: kotak harus meliputi HANYA tumpukan mika, tanpa
    tutup atas dan tanpa kaki hitam di bawahnya. Kalau kaki ikut terkotak,
    semua batas segmen bergeser dan keempat pembacaan salah dengan cara yang
    tetap terlihat masuk akal.
    """
    x, y, w, h = tower_px
    tinggi = h / float(n)
    return [(x, int(y + i * tinggi), w, max(1, int(tinggi))) for i in range(n)]


def _ukur(frame, roi):
    """(persentil ke-90 kanal V, rata-rata S) di dalam ROI."""
    x, y, w, h = roi
    H, W = frame.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return 0.0, 0.0
    hsv = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    return float(np.percentile(hsv[:, :, 2], 90)), float(np.mean(hsv[:, :, 1]))


class PembacaMenara:
    """Membaca satu menara dan mengingat riwayatnya untuk menilai kedip.

    Satu instance per mesin, dipakai ulang antar frame — riwayat kedip
    tersimpan di dalamnya.
    """

    def __init__(self, no, tower, indikator,
                 delta_baseline=DELTA_BASELINE, delta_tetangga=DELTA_TETANGGA,
                 v_minimum=V_MINIMUM, jendela_kedip=JENDELA_KEDIP):
        self.no = no
        self.tower = tower                  # [x, y, w, h] persen
        self.indikator = list(indikator)    # urut ATAS -> BAWAH
        self.n = len(indikator)
        self.delta_baseline = delta_baseline
        self.delta_tetangga = delta_tetangga
        self.v_minimum = v_minimum
        self.jendela = jendela_kedip
        self.baseline = None
        self._riwayat = [deque() for _ in range(self.n)]

    # ---------- kalibrasi ----------
    def rekam_baseline(self, frame):
        """Rekam kecerahan saat menara padam.

        Di pabrik ini keadaan normal memang gelap, jadi baseline bisa diambil
        kapan saja mesin sedang berproduksi.
        """
        h, w = frame.shape[:2]
        seg = bagi_segmen(rect_from_percent(self.tower, w, h), self.n)
        self.baseline = [_ukur(frame, r)[0] for r in seg]
        return self.baseline

    # ---------- pembacaan ----------
    def baca(self, frame, now=None):
        now = time.time() if now is None else now
        h, w = frame.shape[:2]
        seg_px = bagi_segmen(rect_from_percent(self.tower, w, h), self.n)

        ukur = [_ukur(frame, r) for r in seg_px]
        v = [u[0] for u in ukur]

        hasil = []
        for i in range(self.n):
            # median segmen LAIN — median, bukan rata-rata, supaya satu segmen
            # menyala terang tidak menaikkan acuan bagi segmen menyala lainnya
            lain = [v[j] for j in range(self.n) if j != i]
            acuan = float(np.median(lain)) if lain else 0.0

            lewat_tetangga = (v[i] - acuan) >= self.delta_tetangga
            lewat_baseline = bool(self.baseline) and \
                (v[i] - self.baseline[i]) >= self.delta_baseline
            terang = (lewat_baseline or lewat_tetangga) and v[i] >= self.v_minimum

            self._catat(i, now, terang)
            keadaan = self._keadaan(i, now)
            hasil.append({
                "no": self.no,
                "seg": i,
                "indikator": self.indikator[i],
                "keadaan": keadaan,
                "aktif": keadaan in (NYALA, KEDIP),
                "v90": round(v[i], 1),
                "sat": round(ukur[i][1], 1),
                "baseline": round(self.baseline[i], 1) if self.baseline else None,
                "acuan": round(acuan, 1),
                # jarak ke ambang — angka inilah yang dipakai menyetel di lapangan
                "margin": round(v[i] - acuan - self.delta_tetangga, 1),
                "roi": seg_px[i],
            })
        return hasil

    # ---------- kedip ----------
    def _catat(self, i, now, terang):
        d = self._riwayat[i]
        d.append((now, bool(terang)))
        while d and now - d[0][0] > self.jendela:
            d.popleft()

    def _keadaan(self, i, now):
        d = self._riwayat[i]
        if not d:
            return PADAM

        # Gelap cepat: kalau sedetik terakhir semuanya gelap, jangan menunggu
        # jendela penuh. "Setup selesai" adalah peristiwa yang ingin ditangkap
        # tepat waktu, bukan tiga detik kemudian.
        baru = [x[1] for x in d if now - x[0] <= GELAP_CEPAT]
        if baru and not any(baru):
            return PADAM

        nilai = [x[1] for x in d]
        transisi = sum(1 for a, b in zip(nilai, nilai[1:]) if a != b)
        if transisi >= MIN_TRANSISI:
            return KEDIP
        return NYALA if nilai[-1] else PADAM


# ---------------------------------------------------------------- status
#: peran tiap indikator — menentukan status mesin saat segmen itu menyala
NORMAL, SETUP, MASALAH = "normal", "setup", "masalah"

#: tebakan peran dari nama, dipakai kalau `peran` tidak diisi eksplisit
_KATA_NORMAL = ("normal", "jalan", "run", "hijau", "green", "ready")
_KATA_SETUP = ("setup", "setting", "inisialisasi", "init")


def _peran(nama, peran):
    if peran and nama in peran:
        return peran[nama]
    n = nama.lower()
    if any(k in n for k in _KATA_SETUP):
        return SETUP
    if any(k in n for k in _KATA_NORMAL):
        return NORMAL
    return MASALAH


def status_mesin(bacaan, peran=None, saat_gelap="run"):
    """Turunkan status mesin dari segmen mana yang aktif.

    Kembalian: (status, daftar_indikator_aktif)

    `saat_gelap` — status ketika TIDAK ADA segmen menyala. Ini titik paling
    menentukan di seluruh modul, dan jawabannya berbeda antar pabrik:

        "run"  menara hanya menyala kalau ada sesuatu; hijau cuma berkedip
               saat setup lalu padam. SEMUA PADAM = MESIN JALAN.
        "off"  hijau menyala terus selama produksi. Semua padam berarti mesin
               mati atau lampunya putus — BUKAN kabar baik.

    Salah memilih membuat status setiap mesin terbalik, dan hasilnya tetap
    terlihat masuk akal di layar. Pastikan dengan melihat satu mesin yang
    sedang berproduksi normal.

    `peran` — {"nama_indikator": NORMAL | SETUP | MASALAH}. Kalau kosong,
    peran ditebak dari nama indikatornya.
    """
    aktif = [b["indikator"] for b in bacaan if b["aktif"]]
    if not aktif:
        return saat_gelap, []

    peran_aktif = [_peran(a, peran) for a in aktif]
    if MASALAH in peran_aktif:
        return "stop", aktif
    if SETUP in peran_aktif:
        return "idle", aktif
    return "run", aktif


def read_tower(frame, pembaca, now=None, peran=None, saat_gelap="run"):
    """Baca banyak menara sekaligus.

    pembaca : daftar PembacaMenara (satu per mesin)
    Return  : (hasil_per_mesin, ringkasan_jumlah)
    """
    out, counts = [], {"run": 0, "idle": 0, "stop": 0, "off": 0}
    for p in pembaca:
        bacaan = p.baca(frame, now)
        status, aktif = status_mesin(bacaan, peran, saat_gelap)
        counts[status] += 1
        out.append({
            "no": p.no,
            "status": status,
            "indikator": aktif,            # mis. ["putus_pakan"]
            "kedip": any(b["keadaan"] == KEDIP for b in bacaan),
            "segmen": bacaan,
        })
    return out, counts


def buat_pembaca(machines, indikator, ambang=None):
    """Bangun daftar PembacaMenara dari konfigurasi kalibrasi.

    machines : [{"no": 1, "tower": [x, y, w, h]}, ...]
               Format lama {"no": 1, "lamp": [...]} tetap diterima dan
               diperlakukan sebagai menara satu segmen.
    """
    ambang = ambang or {}
    keluar = []
    for m in machines:
        kotak = m.get("tower") or m.get("lamp")
        if not kotak:
            continue
        ind = m.get("indikator") or indikator
        if m.get("lamp") and not m.get("tower"):
            ind = ind[:1] or ["lampu"]      # format lama: satu segmen
        keluar.append(PembacaMenara(m["no"], kotak, ind, **ambang))
    return keluar


# ---------------------------------------------------------------- overlay
def draw_lamps(frame, hasil, show_label=True, show_ratio=True):
    """Gambar kotak segmen + angka ukur, untuk verifikasi mata.

    `show_ratio` menampilkan V90 terukur. Angka inilah yang dibandingkan
    dengan ambang, jadi tanpa menampilkannya penyetelan di lapangan hanya
    tebak-tebakan: tidak ada cara melihat seberapa jauh pembacaan dari
    ambangnya.
    """
    for mesin in hasil:
        segmen = mesin["segmen"] if isinstance(mesin, dict) and "segmen" in mesin \
            else [mesin]
        for b in segmen:
            x, y, w, h = b["roi"]
            col = DRAW.get(b["keadaan"], DRAW[PADAM])
            cv2.rectangle(frame, (x, y), (x + w, y + h), col, 2)
            if show_label:
                teks = b["indikator"][:10]
                if b["keadaan"] == KEDIP:
                    teks += "*"             # tanda kedip
                if show_ratio:
                    teks += " %.0f" % b["v90"]
                cv2.putText(frame, teks, (x + w + 4, y + int(h * 0.7)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.34, col, 1, cv2.LINE_AA)
        if show_label and segmen:
            x, y = segmen[0]["roi"][0], segmen[0]["roi"][1]
            cv2.putText(frame, "M%02d" % mesin.get("no", 0), (x, max(11, y - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (220, 220, 220), 1, cv2.LINE_AA)
    return frame
