"""Penyimpanan penugasan MO (Manufacturing Order) per mesin.

Sebelumnya nomor MO DIKARANG acak saat dashboard menyala, jadi angka di
layar berganti tiap restart dan tidak pernah cocok dengan kertas order di
tangan PPIC. Di sini MO diisi manusia dan bertahan.

Bentuk simpanannya sengaja dibuat sederhana:

    {"ajl-01": {"1": "MO-5285", "2": "MO-5285", "7": "MO-6951"}}

Mesin yang tidak disebut berarti BELUM DITUGASI — bukan "MO kosong".
Bedanya penting: layar harus bisa menunjukkan mesin yang terlewat saat
pembagian order, bukan menyamarkannya jadi sel kosong.

TODO(pabrik): kalau nanti ada ERP/PPIC yang sudah memegang data ini,
ambil dari sana dan jadikan berkas ini hanya cadangan. Mengetik ulang
order di dua tempat adalah cara paling cepat membuat keduanya berbeda.
"""
import json
import logging
import re
import threading
from pathlib import Path
from typing import Dict, Optional

log = logging.getLogger("cctv")

#: Bentuk nomor MO yang diterima. Longgar (huruf, angka, strip, garis bawah,
#: titik) karena penomoran order berbeda tiap pabrik — yang dijaga hanya
#: panjang dan karakter aneh, supaya tidak ada yang menitipkan HTML ke layar.
POLA_MO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/-]{0,31}$")


def bersihkan_mo(nilai) -> str:
    """Nomor MO yang aman dipakai, atau "" kalau tidak masuk akal."""
    teks = str(nilai or "").strip()
    if not teks:
        return ""
    return teks if POLA_MO.match(teks) else ""


class MoStore:
    def __init__(self, path: str = "data/mo.json"):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: Dict[str, Dict[str, str]] = {}
        self.load()

    # ---------- baca / tulis berkas ----------
    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            mentah = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            log.warning("Gagal memuat MO (%s) — mulai dari kosong", e)
            return
        bersih: Dict[str, Dict[str, str]] = {}
        for line_id, mesin in (mentah or {}).items():
            if not isinstance(mesin, dict):
                continue
            isi = {}
            for no, mo in mesin.items():
                m = bersihkan_mo(mo)
                if m and str(no).isdigit():
                    isi[str(int(no))] = m
            if isi:
                bersih[str(line_id)] = isi
        self._data = bersih
        log.info("MO dimuat: %d line dari %s", len(self._data), self.path)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False))
        tmp.replace(self.path)          # atomik: tidak korup saat mati listrik

    # ---------- akses ----------
    def get(self, line_id: str) -> Dict[str, str]:
        with self._lock:
            return dict(self._data.get(line_id) or {})

    def all(self) -> Dict[str, Dict[str, str]]:
        with self._lock:
            return {k: dict(v) for k, v in self._data.items()}

    def set(self, line_id: str, mesin: dict) -> Dict[str, str]:
        """Ganti seluruh penugasan satu line.

        Nilai kosong berarti mesin itu DILEPAS dari penugasan, bukan
        diberi MO bernama "". Itu cara operator membatalkan salah isi.
        """
        isi: Dict[str, str] = {}
        for no, mo in (mesin or {}).items():
            if not str(no).isdigit():
                continue
            m = bersihkan_mo(mo)
            if m:
                isi[str(int(no))] = m
        with self._lock:
            if isi:
                self._data[line_id] = isi
            else:
                self._data.pop(line_id, None)
            self._save()
        log.info("MO line %s disimpan: %d mesin ditugasi", line_id, len(isi))
        return isi

    def delete(self, line_id: str) -> bool:
        with self._lock:
            ada = self._data.pop(line_id, None) is not None
            if ada:
                self._save()
        return ada

    def terapkan(self, line) -> None:
        """Tempelkan MO tersimpan ke objek Line.

        Mesin yang tidak ada di simpanan dikosongkan — supaya MO yang
        dihapus di portal benar-benar hilang dari layar, bukan menempel
        sampai dashboard di-restart.
        """
        isi = self.get(line.id)
        for m in line.machines:
            m.order_mo = isi.get(str(m.no), "")
