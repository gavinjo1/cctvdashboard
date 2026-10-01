"""Riwayat, laporan shift, dan ringkasan periode.

Semuanya BACA SAJA. Angkanya berasal dari database (periode yang sudah
selesai) atau dari akumulator di memori (periode yang sedang berjalan) —
laporan ditulis ke database saat shift berakhir, bukan terus-menerus.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..report import build_report
from ..state import accumulator, history, schedule

log = logging.getLogger("cctv")
router = APIRouter()


@router.get("/api/history/production")
async def api_history_production(line_id: str = None, hours: int = 24):
    return JSONResponse(history.production_history(line_id, hours))


@router.get("/api/history/alerts")
async def api_history_alerts(line_id: str = None, days: int = 7):
    return JSONResponse(history.alert_history(line_id, days))


@router.get("/api/history/alert-stats")
async def api_alert_stats(days: int = 7):
    """Rekap alert: jumlah, persen false alarm, rata-rata waktu tanggap."""
    return JSONResponse(history.alert_stats(days))


@router.get("/api/shift")
async def api_shift():
    return JSONResponse(schedule.info())


@router.get("/api/report/periods")
async def api_report_periods(limit: int = 60):
    """Daftar shift yang laporannya tersedia, ditambah periode berjalan."""
    rows = history.report_periods(limit)
    info = schedule.info()
    live = {
        "period_key": info["period_key"],
        "shift": info["name"],
        "started_at": schedule.started_at(datetime.now())
                              .isoformat(timespec="seconds"),
        "ended_at": None,
        "baris": 0,
        "live": True,
    }
    rows = [r for r in rows if r["period_key"] != info["period_key"]]
    return JSONResponse([live] + rows)


@router.get("/api/report/shift")
async def api_report_shift(period_key: str = None):
    """Laporan satu shift. Tanpa parameter = periode yang sedang berjalan."""
    now = datetime.now()
    current = schedule.key_at(now)
    key = period_key or current

    if key == current:
        # periode berjalan: hitung langsung dari akumulator di memori
        rep = accumulator.live_report()
    else:
        rows = history.shift_report_rows(key)
        if not rows:
            raise HTTPException(
                status_code=404,
                detail="Laporan periode %s belum ada. Laporan ditulis saat "
                       "shift berakhir." % key)
        rep = build_report(key, rows[0]["shift"], rows)

    if rep.get("started_at"):
        rep["alerts"] = history.alert_stats_period(
            key, rep["started_at"], rep["ended_at"] or now.isoformat())
    return JSONResponse(rep)


@router.get("/api/shift/summary")
async def api_shift_summary(period_key: str = None):
    key = period_key or schedule.key_at(datetime.now())
    return JSONResponse({"period_key": key,
                         "lines": history.shift_summary(key)})
