"""Keadaan line & mesin — dibaca dashboard, ditulis AI worker."""
import logging

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse

from ..auth import require_api_key
from ..config import settings
from ..source import _angka
from ..state import manager, snapshot, source, tracker

log = logging.getLogger("cctv")
router = APIRouter()


@router.get("/api/lines")
async def api_lines():
    return JSONResponse(snapshot())


@router.get("/api/lines/{line_id}")
async def api_line(line_id: str):
    line = source.get_line(line_id)
    if line is None:
        raise HTTPException(status_code=404, detail="Line tidak ditemukan")
    return JSONResponse(line.to_dict())


@router.get("/api/summary")
async def api_summary():
    return JSONResponse(source.summary())


@router.post("/api/lines/{line_id}/machines",
             dependencies=[Depends(require_api_key)])
async def api_machine_status(line_id: str, payload: dict = Body(...)):
    """Status mesin hasil pembacaan lampu tower oleh AI worker.

    Body: {"orang": 1,
           "machines": [{"no": 1, "status": "run", "color": "merah",
                         "sejak": 1758000000.0}, ...]}

    `sejak` = jam transisi menurut worker (epoch). Dipakai menggantikan jam
    kedatangan paket, supaya catatan tidak meleset sepanjang jeda kirim.
    `orang` = jumlah orang di zona line saat itu; dipakai menandai episode
    bermasalah yang sedang berjalan bahwa operator sudah datang.
    """
    machines = payload.get("machines")
    if not isinstance(machines, list):
        raise HTTPException(status_code=400,
                            detail="field 'machines' wajib berupa list")
    n = source.apply_machine_status(line_id, machines)
    if n < 0:
        raise HTTPException(status_code=404, detail="Line tidak ditemukan")

    # catat perubahan warna sebagai episode untuk pengumpulan data uji
    if settings.EVENT_LOG in ("vision", "all"):
        tracker.observe_many(line_id, machines,
                             orang=_angka(payload.get("orang"), 0, 999),
                             orang_sejak=payload.get("orang_sejak"))
    await manager.broadcast(snapshot())
    return {"status": "ok", "updated": n}
