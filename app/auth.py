"""Autentikasi endpoint tulis + identitas operator untuk jejak audit.

Dua lapis yang berbeda tujuan:

  1. API key  — untuk MESIN (AI worker). Melindungi DUA endpoint saja:
     POST /api/alerts dan POST /api/lines/{id}/machines. Tanpa ini siapa
     pun di jaringan pabrik bisa mengirim alert palsu dan mengubah status
     mesin.

  2. Nama operator — untuk ORANG. Bukan pengaman, tapi pencatat: siapa
     yang menekan "Tindak Lanjuti" atau "False Alarm".
     TODO(pabrik): kalau butuh pengamanan sungguhan (bukan sekadar
     pencatatan), sambungkan ke LDAP/Active Directory pabrik di sini.

YANG BELUM TERLINDUNGI SAMA SEKALI
----------------------------------
Endpoint yang dipanggil dari browser tidak diperiksa siapa pun:

    PUT    /api/lines/{id}/calibration   ubah zona & ROI lampu
    DELETE /api/lines/{id}/calibration   hapus kalibrasi satu kamera
    DELETE /api/log/{id}                 kosongkan catatan deteksi
    POST   /api/alerts/{id}/resolve      tutup alert atas nama siapa saja

API key tidak bisa dipakai di sana: nilainya harus ikut ke setiap browser
yang membuka dashboard, dan itu sama saja dengan mengumumkannya. Yang
dibutuhkan adalah sesi/login, bukan kunci mesin.

Keputusan saat ini: DIBIARKAN TERBUKA, dengan andalan jaringan pabrik yang
terisolasi. Lihat "Endpoint yang masih terbuka" di PRODUCTION.md sebelum
dashboard ini dijangkau jaringan yang lebih luas.
"""
import hmac
import json
import logging
from pathlib import Path
from typing import List, Optional

from fastapi import Header, HTTPException

from .config import settings

log = logging.getLogger("cctv")


#: peringatan mode terbuka hanya dicetak sekali, bukan tiap permintaan
_warned_open = False


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    """Dependency untuk endpoint yang dipanggil AI worker.

    Dipakai dengan header:  X-API-Key: <token>

    Bila CCTV_API_KEY belum diisi, permintaan DITERIMA dan sebuah peringatan
    dicetak sekali ke log. Menolaknya akan membuat sistem tidak berfungsi
    sama sekali saat baru dijalankan, dengan kode kesalahan yang terbaca
    seperti kerusakan server. Kekosongan kunci sudah ditandai keras saat
    startup dan di GET /healthz, jadi tidak mungkin lolos diam-diam ke
    produksi. Set CCTV_REQUIRE_API_KEY=true bila tetap ingin ditolak.
    """
    global _warned_open

    if not settings.API_KEY:
        if settings.REQUIRE_API_KEY:
            raise HTTPException(
                status_code=503,
                detail="CCTV_API_KEY belum diisi dan CCTV_REQUIRE_API_KEY=true, "
                       "sehingga endpoint tulis dinonaktifkan. Isi kunci, atau "
                       "set CCTV_REQUIRE_API_KEY=false.")
        if not _warned_open:
            _warned_open = True
            log.warning("CCTV_API_KEY kosong — endpoint tulis TERBUKA untuk "
                        "siapa pun di jaringan ini. Boleh untuk uji coba, "
                        "isi kunci sebelum dipakai di pabrik.")
        return

    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail="Header X-API-Key wajib. AI worker: isi 'api_key' di "
                   "ai/zones.json, atau jalankan webcam_demo.py dengan "
                   "--api-key <token>.")
    if not hmac.compare_digest(x_api_key, settings.API_KEY):
        raise HTTPException(status_code=401, detail="X-API-Key tidak cocok")


class OperatorDirectory:
    """Daftar nama yang boleh menutup alert.

    Disimpan sebagai file JSON sederhana: ["Budi S.", "Rina M.", ...]
    Kalau file tidak ada, nama apa pun diterima dan hanya dicatat.
    """

    def __init__(self, path: str = None):
        self.path = Path(path or settings.OPERATORS_FILE)
        self.names: List[str] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            log.info("Daftar operator (%s) tidak ada — nama bebas, "
                     "tetap dicatat di log audit", self.path)
            return
        try:
            data = json.loads(self.path.read_text())
            self.names = [str(x) for x in data] if isinstance(data, list) else []
            log.info("Daftar operator dimuat: %d nama", len(self.names))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("Gagal memuat daftar operator (%s)", e)

    def valid(self, name: str) -> bool:
        if not name or not name.strip():
            return False
        if not self.names:
            return True                 # tidak ada daftar: terima apa adanya
        return name in self.names

    def all(self) -> List[str]:
        return list(self.names)


#: satu instance dipakai app/main.py untuk memvalidasi nama saat menutup alert
operators = OperatorDirectory()
