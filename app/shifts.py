"""Jadwal shift dan penentuan kapan penghitung harus dinolkan.

Tanpa ini, `output` dan `stops` menumpuk selamanya dan angka
"Output Hari Ini" akan terus naik sampai tidak masuk akal.
"""
import logging
from datetime import datetime, time, timedelta
from typing import List, Optional, Tuple

from .config import settings

log = logging.getLogger("cctv")


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


class ShiftSchedule:
    """Jadwal shift pabrik.

    Shift boleh melewati tengah malam (mis. 22:00-06:00) — ditangani
    dengan membandingkan menit-dalam-hari secara melingkar.
    """

    def __init__(self, starts: List[str] = None, names: List[str] = None):
        starts = starts or settings.SHIFT_STARTS
        names = names or settings.SHIFT_NAMES

        if not starts:
            starts, names = ["00:00"], ["Shift 1"]
        if len(names) != len(starts):
            log.warning("Jumlah nama shift (%d) != jumlah jam mulai (%d) — "
                        "nama shift dibuat otomatis", len(names), len(starts))
            names = ["Shift %d" % (i + 1) for i in range(len(starts))]

        try:
            self.starts = [_parse_hhmm(s) for s in starts]
        except (ValueError, IndexError):
            log.error("CCTV_SHIFT_STARTS tidak valid (%s) — pakai 06:00", starts)
            self.starts = [time(6, 0)]
            names = ["Shift 1"]

        # urutkan supaya pencarian shift benar
        pairs = sorted(zip(self.starts, names), key=lambda p: p[0])
        self.starts = [p[0] for p in pairs]
        self.names = [p[1] for p in pairs]

    # ---------- pencarian shift ----------
    def index_at(self, now: datetime) -> int:
        """Index shift yang sedang berjalan pada waktu `now`."""
        cur = now.time()
        idx = len(self.starts) - 1          # sebelum shift pertama = shift terakhir kemarin
        for i, st in enumerate(self.starts):
            if cur >= st:
                idx = i
        return idx

    def name_at(self, now: datetime) -> str:
        return self.names[self.index_at(now)]

    def started_at(self, now: datetime) -> datetime:
        """Waktu mulai shift yang sedang berjalan (bisa kemarin)."""
        i = self.index_at(now)
        start = datetime.combine(now.date(), self.starts[i])
        if start > now:                      # shift dimulai kemarin
            start -= timedelta(days=1)
        return start

    def key_at(self, now: datetime) -> str:
        """Penanda unik periode berjalan, mis. '2026-09-10#1'.

        Berubahnya nilai ini = saatnya menolkan penghitung.
        """
        if settings.RESET_MODE == "day":
            reset_at = _parse_hhmm(settings.DAY_RESET_AT)
            day = now.date()
            if now.time() < reset_at:
                day -= timedelta(days=1)
            return "%s#day" % day.isoformat()
        s = self.started_at(now)
        return "%s#%d" % (s.date().isoformat(), self.index_at(now))

    def name_of_key(self, period_key: str) -> str:
        """Nama shift dari period_key, mis. '2026-09-10#1' -> 'Shift 2'.

        Dipakai saat menyimpan produksi periode yang BARU SAJA berakhir —
        kalau memakai nama shift berjalan, data periode lama akan tercatat
        dengan label shift yang salah.
        """
        try:
            tail = period_key.split("#", 1)[1]
            if tail == "day":
                return "Harian"
            return self.names[int(tail)]
        except (IndexError, ValueError):
            return "-"

    def info(self, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now()
        started = self.started_at(now)
        i = self.index_at(now)
        nxt = self.starts[(i + 1) % len(self.starts)]
        next_dt = datetime.combine(now.date(), nxt)
        if next_dt <= now:
            next_dt += timedelta(days=1)
        return {
            "name": self.names[i],
            "index": i,
            "started_at": started.strftime("%H:%M"),
            "next_at": next_dt.strftime("%H:%M"),
            "elapsed_min": int((now - started).total_seconds() // 60),
            "remaining_min": int((next_dt - now).total_seconds() // 60),
            "period_key": self.key_at(now),
            "reset_mode": settings.RESET_MODE,
        }


class ResetTracker:
    """Deteksi pergantian periode; memberi tahu kapan harus reset."""

    def __init__(self, schedule: ShiftSchedule, initial_key: str = None):
        self.schedule = schedule
        self.current_key = initial_key or schedule.key_at(datetime.now())

    def due(self, now: Optional[datetime] = None) -> Tuple[bool, str, str]:
        """(perlu_reset, key_lama, key_baru)"""
        now = now or datetime.now()
        new_key = self.schedule.key_at(now)
        if new_key == self.current_key:
            return False, self.current_key, new_key
        old = self.current_key
        self.current_key = new_key
        return True, old, new_key
