"""Menarik frame dari satu stream kamera, dengan penyambungan ulang.

Semua urusan "stream putus, coba lagi, jangan banjiri log" ada di sini,
terpisah dari apa yang dilakukan terhadap framenya. Worker tinggal
mengambil frame; ia tidak perlu tahu soal timeout OpenCV, jeda mundur
berlipat, atau kapan peringatan harus didiamkan.
"""
import logging
import time

import cv2

log = logging.getLogger("ai")

#: jeda pertama sebelum mencoba membuka stream lagi (detik)
OPEN_RETRY_DELAY = 10

#: Jeda menyambung ulang DILIPATDUAKAN tiap kegagalan berturut-turut,
#: sampai sebanyak ini. Kamera yang memang tidak ada (dicabut, salah IP,
#: line belum disajikan) dulu dicoba tiap 10 detik SELAMANYA — 8 kamera
#: mati menghasilkan ribuan baris peringatan per jam, dan log yang banjir
#: membuat kesalahan yang benar-benar baru tidak terlihat.
OPEN_RETRY_MAX = 300

#: Setelah sekian kegagalan berturut-turut, peringatan diturunkan ke DEBUG.
#: Keadaannya sudah dilaporkan; mengulanginya tidak menambah informasi.
DIAM_SETELAH = 3

#: batas waktu buka & baca stream (milidetik). Tanpa ini OpenCV bisa
#: menggantung selamanya pada kamera yang tidak menjawab: thread tetap
#: terlihat "hidup" padahal tidak pernah memproses frame lagi.
OPEN_TIMEOUT_MS = 10000
READ_TIMEOUT_MS = 10000


class Kamera:
    """Pembuka stream satu kamera, ingat berapa kali gagal berturut-turut."""

    def __init__(self, rtsp, line_id, stop_flag):
        self.rtsp = rtsp
        self.line_id = line_id
        self.stop_flag = stop_flag
        self.gagal_buka = 0       # kegagalan membuka berturut-turut

    def buka(self):
        """Buka stream. Return VideoCapture, atau None kalau gagal.

        Kalau gagal, fungsi ini SUDAH menunggu sesuai jeda mundurnya —
        pemanggil tinggal mencoba lagi tanpa perlu tidur sendiri.
        """
        cap = cv2.VideoCapture(self.rtsp, cv2.CAP_FFMPEG)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_TIMEOUT_MS)
        cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, READ_TIMEOUT_MS)

        if not cap.isOpened():
            cap.release()
            self._laporkan_gagal()
            return None

        if self.gagal_buka:
            log.info("[%s] stream terhubung lagi setelah %d kali gagal",
                     self.line_id, self.gagal_buka)
            self.gagal_buka = 0
        else:
            log.info("[%s] stream terhubung", self.line_id)
        return cap

    def jeda_berikutnya(self):
        """Jeda mundur berlipat untuk kegagalan ke-N, dijepit ke OPEN_RETRY_MAX."""
        return min(OPEN_RETRY_MAX,
                   OPEN_RETRY_DELAY * (2 ** max(0, self.gagal_buka - 1)))

    def _laporkan_gagal(self):
        self.gagal_buka += 1
        jeda = self.jeda_berikutnya()
        if self.gagal_buka <= DIAM_SETELAH:
            log.warning("[%s] tidak bisa membuka stream (%d kali), "
                        "coba lagi %d detik",
                        self.line_id, self.gagal_buka, jeda)
            if self.gagal_buka == DIAM_SETELAH:
                log.warning("[%s] peringatan berikutnya ditahan sampai "
                            "stream hidup lagi — jalankan dengan --verbose "
                            "kalau perlu melihatnya", self.line_id)
        else:
            log.debug("[%s] masih gagal (%d kali), coba lagi %d detik",
                      self.line_id, self.gagal_buka, jeda)
        self.stop_flag.wait(jeda)


class Pengatur:
    """Penentu frame mana yang diproses, dari dua laju berbeda.

    DUA LAJU. Lampu dibaca tiap frame karena sinyalnya KEDIP — pada 3 fps,
    kedip 3 Hz tidak terdeteksi sama sekali. Deteksi orang jauh lebih mahal
    (YOLO) dan tidak perlu secepat itu.

    Laju lampu hanya dipakai kalau kamera ini PUNYA menara untuk dibaca.
    Kamera yang belum dikalibrasi tidak punya alasan mendekode 25 frame per
    detik lalu membuangnya — dulu semua kamera dipaksa ke laju lampu, dan
    12 dari 18 kamera di zones.json membayar dekode 8x lipat untuk frame
    yang tidak dipakai siapa pun.
    """

    def __init__(self, fps_orang, fps_lampu):
        self.interval_orang = 1.0 / max(1, fps_orang)
        self.interval_lampu = 1.0 / max(1, fps_lampu or fps_orang)
        self._proses_terakhir = 0.0
        self._orang_terakhir = 0.0

    def perlu_proses(self, now, ada_menara):
        """Frame ini layak didekode?"""
        interval = self.interval_lampu if ada_menara else self.interval_orang
        if now - self._proses_terakhir < interval:
            return False
        self._proses_terakhir = now
        return True

    def perlu_orang(self, now):
        """Frame ini layak dijalankan deteksi orang (yang mahal)?"""
        if now - self._orang_terakhir < self.interval_orang:
            return False
        self._orang_terakhir = now
        return True
