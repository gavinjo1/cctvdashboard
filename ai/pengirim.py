"""Satu-satunya bagian yang berbicara HTTP ke dashboard.

Dipisah dari worker supaya:
  * aturan alert (ai/aturan.py) bisa diuji tanpa jaringan sama sekali
  * kalau protokol dashboard berubah, yang disentuh cuma berkas ini
  * cooldown alert dan hasil pengirimannya tinggal di satu tempat — dulu
    keduanya terserak di CameraWorker dan gampang terpisah tanpa sengaja

Cooldown SENGAJA ada di sini, bukan di ai/aturan.py. Cooldown bergantung
pada apakah kiriman BERHASIL; memisahkannya dari pengiriman pernah membuat
satu kegagalan jaringan membuang alert sekaligus menutup label itu selama
cooldown penuh.
"""
import logging
import time
from datetime import datetime

import requests

log = logging.getLogger("ai")

#: Jeda sebelum alert yang GAGAL TERKIRIM dicoba lagi. Jauh lebih pendek dari
#: cooldown_seconds: kegagalan jaringan tidak boleh menyembunyikan kejadian.
JEDA_ULANG_ALERT = 30

#: batas waktu tiap permintaan ke dashboard (detik)
TIMEOUT = 5


class Pengirim:
    """Klien HTTP ke dashboard untuk SATU line.

    Satu Session per line: koneksi TCP dipakai ulang (keep-alive), bukan
    dibuka-tutup tiap kiriman. requests.post() tingkat modul membuat Session
    baru tiap panggilan lalu membuangnya. Di localhost bedanya tipis
    (2,96 -> 2,51 ms), tetapi worker di pabrik biasanya di MESIN LAIN — di
    situ jabat tangan TCP tiap 10 detik x 18 kamera baru terasa, dan tiap
    koneksi meninggalkan socket TIME_WAIT.
    """

    def __init__(self, dashboard_url, line_id, api_key="", cooldown=600):
        self.dashboard = dashboard_url.rstrip("/")
        self.line_id = line_id
        self.cooldown = cooldown
        # TODO(pabrik): api_key WAJIB diisi di zones.json, sama dengan
        # CCTV_API_KEY di dashboard. Tanpa ini endpoint tulis menolak.
        self.headers = {"X-API-Key": api_key} if api_key else {}
        self.http = requests.Session()
        self.last_alert = {}          # label -> waktu terakhir dikirim

    # ---------- kalibrasi ----------
    def ambil_kalibrasi(self):
        """Kalibrasi terbaru dari dashboard, atau None.

        None berarti "tidak ada yang baru atau tidak bisa diambil" — pemanggil
        harus tetap memakai kalibrasi yang lama. Dashboard mati tidak boleh
        membuat worker kehilangan zona dan berhenti membaca lampu.
        """
        try:
            r = self.http.get(
                f"{self.dashboard}/api/lines/{self.line_id}/calibration",
                timeout=TIMEOUT)
            if r.status_code == 404:
                return None             # belum dikalibrasi, pakai zones.json
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError):
            return None                 # dashboard mati: pakai yang terakhir

    # ---------- alert ----------
    def kirim_alert(self, label, activity, duration, confidence,
                    severity="high", object_type="Person"):
        """Kirim satu alert, dengan cooldown per label.

        Return True kalau benar-benar dikirim (bukan ditahan cooldown).
        """
        now = time.time()
        if now - self.last_alert.get(label, 0) < self.cooldown:
            return False                # jangan spam alert yang sama

        payload = {
            "line_id": self.line_id,
            "label": label,
            "activity": activity,
            "confidence": int(confidence * 100) if confidence <= 1
            else int(confidence),
            "object_type": object_type,
            "duration": int(duration),
            "severity": severity,
            "detected_at": datetime.now().strftime("%H:%M:%S"),
        }
        # Cooldown dipasang HANYA kalau alert benar-benar sampai. Kalau
        # dipasang sebelum POST, satu permintaan gagal (dashboard restart,
        # jaringan putus sesaat) membuang alert itu dan menutup label yang
        # sama selama cooldown penuh — mesin berhenti tanpa penanganan tidak
        # dilaporkan sampai 10 menit, dan tidak ada yang tahu.
        try:
            r = self.http.post(f"{self.dashboard}/api/alerts", json=payload,
                               headers=self.headers, timeout=TIMEOUT)
            if r.ok:
                self.last_alert[label] = now
                log.info("[%s] ALERT terkirim: %s (%ds)",
                         self.line_id, label, duration)
            else:
                # Ditolak isi/kunci: mengulang secepatnya hanya akan
                # membanjiri log dengan penolakan yang sama.
                self.last_alert[label] = now
                log.warning("[%s] dashboard menolak: %s %s",
                            self.line_id, r.status_code, r.text[:120])
        except requests.RequestException as e:
            # Gagal kirim = coba lagi sebentar lagi, bukan cooldown penuh.
            self.last_alert[label] = now - self.cooldown + JEDA_ULANG_ALERT
            log.warning("[%s] gagal kirim alert: %s — coba lagi %ds",
                        self.line_id, e, JEDA_ULANG_ALERT)
        return True

    # ---------- status mesin ----------
    def kirim_status(self, machines, orang=0, orang_sejak=None):
        """Kirim status mesin hasil baca lampu.

        JAM TRANSISI IKUT DIKIRIM. Paket ini berangkat tiap status_interval
        detik, tetapi lampu dibaca 25x per detik — jadi worker tahu persis
        detik ke berapa merah menyala. Kalau yang dikirim hanya statusnya,
        dashboard terpaksa memakai jam kedatangan paket dan setiap catatan
        meleset sepanjang jeda pengiriman. Pada kejadian ~1 menit, itu 17%.
        """
        body = {
            "orang": orang,
            # jam kehadiran dimulai, bukan jam paket ini dikirim
            "orang_sejak": orang_sejak,
            "machines": [{"no": r["no"], "status": r["status"],
                          "indikator": r["indikator"],
                          "kedip": r["kedip"],
                          "color": r.get("warna") or "",
                          "sejak": r.get("sejak")} for r in machines],
        }
        try:
            self.http.post(
                f"{self.dashboard}/api/lines/{self.line_id}/machines",
                json=body, headers=self.headers, timeout=TIMEOUT)
            return True
        except requests.RequestException as e:
            log.warning("[%s] gagal kirim status mesin: %s", self.line_id, e)
            return False
