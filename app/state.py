"""Objek bersama yang dipakai semua router, dan bentuk snapshot-nya.

Dipisah dari main.py supaya tiap berkas router bisa mengambil apa yang
dibutuhkannya tanpa mengimpor main.py — kalau router mengimpor main dan
main mengimpor router, keduanya saling menunggu dan aplikasi gagal start.

Berkas ini TIDAK punya route satu pun. Isinya hanya: apa yang hidup selama
aplikasi berjalan, dan bagaimana keadaannya diringkas untuk dikirim ke
peramban.
"""
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import WebSocket

from .calibration import CalibrationStore
from .config import settings
from .eventlog import EpisodeTracker
from .mo import MoStore
from .report import PeriodAccumulator
from .shifts import ResetTracker, ShiftSchedule
from .source import build_source, rollup
from .store import HistoryStore

log = logging.getLogger("cctv")

BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- singleton
source = build_source()
calibration = CalibrationStore(settings.CALIBRATION_FILE)
mo_store = MoStore(settings.MO_FILE)

# MO yang tersimpan ditempelkan SEKALI saat start. Tanpa ini, penugasan
# yang diketik PPIC kemarin hilang tiap dashboard di-restart dan layar
# kembali kosong tanpa penjelasan.
for _line in source.all_lines():
    mo_store.terapkan(_line)
    rollup(_line)

history = HistoryStore(settings.DB_FILE)
schedule = ShiftSchedule()
resetter = ResetTracker(schedule)
tracker = EpisodeTracker(
    on_close=lambda ep: history.save_episode(ep.row()),
    min_seconds=settings.EVENT_MIN_SECONDS,
)
accumulator = PeriodAccumulator(schedule.key_at(datetime.now()),
                                schedule.name_at(datetime.now()),
                                schedule.started_at(datetime.now()))


def asset_version() -> str:
    """Cap waktu file statis terbaru, dipakai sebagai penanda versi di URL.

    Tanpa ini browser operator memakai CSS/JS lama setelah dashboard
    diperbarui, dan perbaikan seolah-olah tidak berpengaruh.
    """
    try:
        newest = max(f.stat().st_mtime
                     for f in (BASE_DIR / "static").rglob("*")
                     if f.is_file())
        return str(int(newest))
    except (OSError, ValueError):
        return "0"


# ---------------------------------------------------------------- websocket
class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.active.append(ws)
        log.info("WS connect — total klien: %d", len(self.active))

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            if ws in self.active:
                self.active.remove(ws)
        log.info("WS disconnect — total klien: %d", len(self.active))

    async def broadcast(self, payload: dict) -> None:
        # JSON dibentuk SEKALI lalu byte yang sama dikirim ke semua klien.
        # Membentuknya per klien akan mengalikan biaya dengan jumlah layar
        # yang terbuka, untuk isi yang persis sama.
        async with self._lock:
            targets = list(self.active)
        if not targets:
            return
        text = json.dumps(payload)
        dead = []
        for ws in targets:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)


manager = ConnectionManager()


# ---------------------------------------------------------------- snapshot
def _line_with_cal(group) -> dict:
    """Sertakan kalibrasi (zona & ROI lampu) dan umur data kamera tiap line."""
    d = group.to_dict()
    for line in d["lines"]:
        line["cal"] = calibration.get(line["id"]) or {"zone": [], "machines": []}
        # umur data dari AI worker — None berarti belum pernah ada kiriman.
        # Dipakai dashboard untuk menandai line yang sudah tidak terpantau.
        line["vision_age"] = source.vision_age(line["id"])
    return d


def snapshot() -> dict:
    """Payload lengkap: struktur group + nilai runtime + ringkasan KPI."""
    return {
        "type": "snapshot",
        "plant": settings.PLANT_NAME,
        "shift": schedule.info(),
        "stream_mode": settings.STREAM_MODE,
        "stream_img_refresh": settings.STREAM_IMG_REFRESH,
        # dipakai dashboard untuk menilai `vision_age` tiap line: lebih tua
        # dari ini berarti AI worker sudah berhenti mengirim.
        "source": settings.SOURCE,
        "vision_lease": source.VISION_LEASE,
        "groups": [_line_with_cal(g) for g in source.groups],
        "halls": source.halls_dict(),
        "summary": source.summary(),
    }
