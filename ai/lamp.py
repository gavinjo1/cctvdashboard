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


def p90(a):
    """Persentil ke-90, tanpa ongkos np.percentile.

    np.percentile pada potongan sekecil ROI lampu menghabiskan 65-73 us
    APA PUN ukurannya — hampir seluruhnya validasi, penanganan sumbu, dan
    interpolasi di dalam NumPy, bukan hitungannya. np.partition mengerjakan
    pemilihan yang sama secara langsung: 2,8-11 us, 6-23 kali lebih cepat.

    Bedanya hanya interpolasi antar dua elemen. Diukur pada ROI lampu
    sungguhan (merah/hijau/putih/padam/latar): selisih terbesar 0,10 dari
    skala 0-255, sementara ambang terkecil yang dipakai modul ini 40.
    """
    a = a.ravel()
    if a.size == 0:
        return 0.0
    if a.size == 1:
        return float(a[0])
    k = int(0.9 * (a.size - 1))
    return float(np.partition(a, k)[k])


def _ukur(frame, roi):
    """(persentil ke-90 kanal V, rata-rata S, potongan HSV) di dalam ROI.

    Potongan HSV ikut dikembalikan supaya warna bisa dihitung belakangan
    TANPA memotong dan mengonversi ulang — dan hanya untuk segmen yang
    memang menyala.
    """
    x, y, w, h = roi
    H, W = frame.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0:
        return 0.0, 0.0, None
    hsv = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    return p90(hsv[:, :, 2]), float(np.mean(hsv[:, :, 1])), hsv


#: Batas hue OpenCV (0-179, jadi setengah derajat) per warna menara.
#: Merah melintasi titik nol, karena itu ditulis sebagai dua rentang.
PITA_WARNA = (
    ("merah",  ((0, 12), (168, 180))),
    ("kuning", ((14, 35),)),
    ("hijau",  ((35, 92),)),
    ("biru",   ((92, 135),)),
)

#: Ambang pada PERSENTIL KE-90 SATURASI, bukan pada rata-rata. Di bawah ini
#: lampu disebut PUTIH. Terukur pada rekaman pabrik:
#:     lampu putih (vid8)  S90 =  48
#:     lampu merah (vid3)   S90 = 192
#:     lampu hijau (vid8)  S90 = 232
SAT_PUTIH = 100.0

#: Piksel ikut dinilai kalau V-nya setidaknya segini dari v90 segmen.
#: Setengah, bukan 0,8: warna sebuah LED justru ada di HALO-nya.
BAGIAN_NYALA = 0.50

#: kecerahan mutlak minimum agar sebuah piksel ikut dinilai
V_PIKSEL_MIN = 60.0


def warna_segmen(hsv, v90):
    """Warna lampu yang menyala di dalam potongan HSV.

    Kembalian: (nama_warna, keyakinan 0-1). None kalau tidak bisa dinilai.

    DUA JEBAKAN, keduanya ditemukan pada rekaman pabrik, bukan dikarang:

    1. INTI LED YANG JENUH ITU PUTIH. Pada lampu merah di vid3.jpeg, inti
       lampu menabrak batas sensor sehingga saturasinya nyaris nol — median
       S seluruh kotak cuma 12. Mengambil piksel PALING TERANG justru
       mengambil bagian yang tidak berwarna, dan lampu merah terbaca putih.
       Warnanya ada di halo. Karena itu penilaian memakai persentil ke-90
       saturasi, dan hue diambil dari piksel paling JENUH — bukan paling
       terang.

    2. HUE ITU MELINGKAR. Merah ada di 175 dan juga di 5. Merata-ratakannya
       secara biasa memberi 90, yaitu cyan: warna yang tidak ada di menara
       mana pun, dan salahnya tidak kelihatan dari angkanya. Dipakai
       rata-rata melingkar lewat jumlah vektor.
    """
    if hsv is None or hsv.size == 0:
        return None, 0.0
    v = hsv[:, :, 2].astype(np.float32)
    s = hsv[:, :, 1].astype(np.float32)
    terang = v >= max(V_PIKSEL_MIN, v90 * BAGIAN_NYALA)
    if not terang.any():
        return None, 0.0

    s90 = p90(s[terang])
    if s90 < SAT_PUTIH:
        return "putih", round(1.0 - s90 / SAT_PUTIH, 4)

    # hue hanya dari piksel paling jenuh: di situlah warna mika terbaca
    pilih = terang & (s >= s90)
    h = hsv[:, :, 0].astype(np.float32)[pilih]
    bobot = s[pilih]
    if h.size == 0:
        return None, 0.0
    sudut = h * (2.0 * np.pi / 180.0)        # 0-179 -> lingkaran penuh
    cx = float(np.sum(np.cos(sudut) * bobot))
    cy = float(np.sum(np.sin(sudut) * bobot))
    if cx == 0.0 and cy == 0.0:
        return None, 0.0
    rerata = (np.arctan2(cy, cx) % (2.0 * np.pi)) * (180.0 / (2.0 * np.pi))

    for nama, pita in PITA_WARNA:
        if any(lo <= rerata < hi for lo, hi in pita):
            cocok = np.zeros(h.shape, dtype=bool)
            for lo, hi in pita:
                cocok |= (h >= lo) & (h < hi)
            return nama, round(float(np.mean(cocok)), 4)
    return None, 0.0


class PembacaMenara:
    """Membaca satu menara dan mengingat riwayatnya untuk menilai kedip.

    Satu instance per mesin, dipakai ulang antar frame — riwayat kedip
    tersimpan di dalamnya.
    """

    def __init__(self, no, tower, indikator,
                 delta_baseline=DELTA_BASELINE, delta_tetangga=DELTA_TETANGGA,
                 v_minimum=V_MINIMUM, jendela_kedip=JENDELA_KEDIP,
                 warna_harapan=None):
        self.no = no
        self.tower = tower                  # [x, y, w, h] persen
        self.indikator = list(indikator)    # urut ATAS -> BAWAH
        self.n = len(indikator)
        self.delta_baseline = delta_baseline
        self.delta_tetangga = delta_tetangga
        self.v_minimum = v_minimum
        self.jendela = jendela_kedip
        self.baseline = None
        # Warna yang SEHARUSNYA ada di tiap posisi, urut atas->bawah.
        # Dipakai untuk memeriksa kotak, bukan untuk menentukan arti.
        self.warna_harapan = list(warna_harapan) if warna_harapan else None
        self._riwayat = [deque() for _ in range(self.n)]
        # Status mesin terakhir dan SEJAK KAPAN. Jam transisi ini yang harus
        # sampai ke dashboard — bukan jam paket dikirim. Lampu dibaca 25x per
        # detik, jadi jam ini teliti; kalau dibuang dan diganti jam kedatangan
        # paket, tiap catatan meleset sepanjang jeda pengiriman.
        self.status_kini = None
        self.status_sejak = None
        # Warna terakhir yang benar-benar TERLIHAT per segmen. Lampu berkedip
        # gelap separuh waktu; kalau warna hanya diambil saat piksel sedang
        # terang, warnanya bolak-balik "hijau" <-> None beberapa kali per
        # detik. Itu me-reset status_sejak terus-menerus dan melahirkan
        # episode baru hampir tiap paket — justru pada keadaan SETUP, yang
        # memang ditandai kedipan.
        self._warna_terakhir = [None] * self.n
        self._yakin_terakhir = [0.0] * self.n

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

            # Warna dihitung HANYA untuk segmen yang menyala. Karena menara
            # padam berarti mesin jalan, keadaan normal di pabrik ini adalah
            # semua segmen gelap — jadi biaya hue mendekati nol sepanjang
            # hari dan hanya dibayar saat ada yang perlu dilihat.
            # Warna diukur saat terang, lalu DIPERTAHANKAN selama segmen
            # masih dianggap aktif — termasuk fase gelap sebuah kedipan.
            # Dilupakan begitu segmen benar-benar padam.
            if terang:
                warna, yakin = warna_segmen(ukur[i][2], v[i])
                self._warna_terakhir[i] = warna
                self._yakin_terakhir[i] = yakin
            elif keadaan in (NYALA, KEDIP):
                warna = self._warna_terakhir[i]
                yakin = self._yakin_terakhir[i]
            else:
                warna, yakin = None, 0.0
                self._warna_terakhir[i] = None
                self._yakin_terakhir[i] = 0.0
            cocok = None
            if warna and self.warna_harapan:
                harap = self.warna_harapan[i]
                cocok = (warna == harap) if harap else None

            hasil.append({
                "no": self.no,
                "seg": i,
                "indikator": self.indikator[i],
                "keadaan": keadaan,
                "aktif": keadaan in (NYALA, KEDIP),
                "v90": round(v[i], 1),
                "sat": round(ukur[i][1], 1),
                # Warna dari piksel — pemeriksa silang, BUKAN pengganti posisi.
                # Kalau segmen ke-2 menyala tapi warnanya merah padahal
                # seharusnya hijau, kotaknya meleset: arti dari posisi jadi
                # salah tanpa satu pun angka terlihat aneh.
                "warna": warna,
                "warna_yakin": round(yakin, 2),
                "warna_cocok": cocok,
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


def warna_utama(bacaan):
    """Warna lampu yang menyala paling atas. None kalau menara padam.

    Dipakai untuk mewarnai mesin di layar sesuai lampu yang benar-benar
    terlihat di lantai — supaya yang dilihat PPIC sama dengan yang dilihat
    operator saat berdiri di depan mesin.
    """
    for b in bacaan:
        if b["aktif"] and b.get("warna"):
            return b["warna"]
    return None


def read_tower(frame, pembaca, now=None, peran=None, saat_gelap="run"):
    """Baca banyak menara sekaligus.

    pembaca : daftar PembacaMenara (satu per mesin)
    Return  : (hasil_per_mesin, ringkasan_jumlah)
    """
    saat = time.time() if now is None else now
    out, counts = [], {"run": 0, "idle": 0, "stop": 0, "off": 0}
    for p in pembaca:
        bacaan = p.baca(frame, now)
        status, aktif = status_mesin(bacaan, peran, saat_gelap)
        counts[status] += 1

        warna = warna_utama(bacaan)
        kunci = (status, warna)
        if p.status_kini != kunci:
            p.status_kini = kunci
            p.status_sejak = saat
        # Segmen yang menyala tetapi warnanya BUKAN warna yang seharusnya
        # ada di posisi itu. Hampir selalu berarti kotak menaranya meleset —
        # arti dari posisi jadi salah, sementara semua angka ukurnya tetap
        # terlihat wajar. Inilah satu-satunya cara menangkapnya tanpa mata.
        salah_warna = [b["indikator"] for b in bacaan
                       if b.get("warna_cocok") is False]
        out.append({
            "no": p.no,
            "status": status,
            "indikator": aktif,            # mis. ["putus_pakan"]
            "kedip": any(b["keadaan"] == KEDIP for b in bacaan),
            # jam saat status/warna ini MULAI — bukan jam pembacaan sekarang
            "sejak": p.status_sejak,
            "warna": warna,
            "warna_segmen": [b.get("warna") for b in bacaan],
            "warna_janggal": salah_warna,
            "segmen": bacaan,
        })
    return out, counts


def buat_pembaca(machines, indikator, ambang=None, warna_harapan=None):
    """Bangun daftar PembacaMenara dari konfigurasi kalibrasi.

    machines : [{"no": 1, "tower": [x, y, w, h]}, ...]
               Format lama {"no": 1, "lamp": [...]} tetap diterima dan
               diperlakukan sebagai menara satu segmen.
    warna_harapan : warna fisik tiap posisi, urut atas->bawah. Dipakai
               memeriksa kotak menara, bukan menentukan arti.
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
        wh = m.get("warna") or warna_harapan
        if wh and len(wh) != len(ind):
            wh = None                       # jangan memaksakan yang tak sepadan
        keluar.append(PembacaMenara(m["no"], kotak, ind,
                                    warna_harapan=wh, **ambang))
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
