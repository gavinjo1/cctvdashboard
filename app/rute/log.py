"""Catatan episode lampu — untuk diunduh dan diperiksa mata.

Ini bukan laporan produksi, melainkan bahan uji: tiap episode berisi kapan
lampu menyala, warnanya apa, berapa lama, dan kapan operator datang.
Dipakai membandingkan bacaan kamera dengan kenyataan di lantai.
"""
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from ..config import settings
from ..eventlog import buat_teks
from ..state import history, source, tracker

log = logging.getLogger("cctv")
router = APIRouter()


@router.get("/api/log/lines")
async def api_log_lines():
    """Line yang sudah punya catatan episode, untuk daftar unduhan."""
    rows = history.episode_lines()
    nama = {l.id: l.name for l in source.all_lines()}
    for r in rows:
        r["name"] = nama.get(r["line_id"], r["line_id"])
        r["berjalan"] = len(tracker.running(r["line_id"]))
    return JSONResponse({"mode": settings.EVENT_LOG, "lines": rows})


@router.get("/api/log/{line_id}.txt", response_class=PlainTextResponse)
async def api_log_txt(line_id: str, hours: int = 0):
    """Unduh catatan deteksi warna lampu sebagai berkas teks.

    hours=0 berarti seluruh catatan yang tersimpan.
    """
    line = source.get_line(line_id)
    if line is None:
        raise HTTPException(status_code=404, detail="Line tidak ditemukan")

    rows = history.episodes(line_id, hours)
    teks = buat_teks(line.name, line_id, rows, tracker.running(line_id))
    nama_berkas = "deteksi-%s-%s.txt" % (
        line_id, datetime.now().strftime("%Y%m%d-%H%M"))
    return PlainTextResponse(
        teks, headers={"Content-Disposition":
                       'attachment; filename="%s"' % nama_berkas})


@router.delete("/api/log/{line_id}")
async def api_log_clear(line_id: str):
    """Kosongkan catatan satu line, untuk memulai sesi pengujian baru."""
    n = history.clear_episodes(line_id)
    tracker.reset(line_id)
    log.info("Catatan episode %s dikosongkan: %d baris", line_id, n)
    return {"status": "ok", "dihapus": n}
