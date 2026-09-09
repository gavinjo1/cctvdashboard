"""Penyimpanan kalibrasi kamera: zona line + ROI lampu tower per line.

Disimpan sebagai satu file JSON supaya:
  * operator bisa mengkalibrasi 18 kamera dari dashboard, satu tempat
  * AI worker tinggal mengambilnya lewat API — tidak perlu menyalin file
    ke server AI setiap kali zona digeser

URL RTSP **tidak** disimpan di sini. Di dalamnya ada password kamera,
sedangkan dashboard dibuka banyak orang di jaringan pabrik. URL kamera
tetap tinggal di file lokal milik AI worker.
"""
import json
import logging
import threading
from pathlib import Path
from typing import Dict, Optional

log = logging.getLogger("cctv")


class CalibrationStore:
    def __init__(self, path: str = "data/calibration.json"):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data: Dict[str, dict] = {}
        self.load()

    # ---------- baca / tulis file ----------
    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            self._data = json.loads(self.path.read_text())
            log.info("Kalibrasi dimuat: %d kamera dari %s",
                     len(self._data), self.path)
        except (OSError, json.JSONDecodeError) as e:
            log.warning("Gagal memuat kalibrasi (%s) — mulai dari kosong", e)
            self._data = {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False))
        tmp.replace(self.path)          # tulis atomik, tidak korup saat mati listrik

    # ---------- akses ----------
    def get(self, line_id: str) -> Optional[dict]:
        return self._data.get(line_id)

    def all(self) -> Dict[str, dict]:
        return dict(self._data)

    def set(self, line_id: str, zone, machines) -> dict:
        """Simpan kalibrasi satu line. zone/machines boleh kosong."""
        entry = {
            "zone": zone or [],
            "machines": machines or [],
        }
        with self._lock:
            self._data[line_id] = entry
            self._save()
        log.info("Kalibrasi %s disimpan: %d titik zona, %d ROI lampu",
                 line_id, len(entry["zone"]), len(entry["machines"]))
        return entry

    def delete(self, line_id: str) -> bool:
        with self._lock:
            if line_id not in self._data:
                return False
            del self._data[line_id]
            self._save()
        return True
