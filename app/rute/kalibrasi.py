"""Kalibrasi kamera: zona line & kotak lampu menara.

Ditulis operator lewat halaman detail kamera, dibaca AI worker tiap 30
detik. Itu sebabnya endpoint baca di sini TIDAK memakai kunci API — worker
harus bisa menariknya bahkan sebelum kuncinya dipasang.
"""
import logging

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

from ..state import calibration, manager, snapshot, source

log = logging.getLogger("cctv")
router = APIRouter()


@router.get("/api/calibration")
async def api_calibration_all():
    """Semua kalibrasi — dipakai AI worker saat start."""
    return JSONResponse(calibration.all())


@router.get("/api/lines/{line_id}/calibration")
async def api_calibration_get(line_id: str):
    cal = calibration.get(line_id)
    if cal is None:
        raise HTTPException(status_code=404, detail="Belum dikalibrasi")
    return JSONResponse(cal)


@router.put("/api/lines/{line_id}/calibration")
async def api_calibration_set(line_id: str, payload: dict = Body(...)):
    """Simpan zona line & ROI lampu hasil kalibrasi dari dashboard.

    Body: {"zone": [[x,y],...], "machines": [{"no":1,"lamp":[x,y,w,h]},...]}
    Koordinat dalam persen (0-100).
    """
    if source.get_line(line_id) is None:
        raise HTTPException(status_code=404, detail="Line tidak ditemukan")

    zone = payload.get("zone", [])
    machines = payload.get("machines", [])
    if not isinstance(zone, list) or not isinstance(machines, list):
        raise HTTPException(status_code=400,
                            detail="'zone' dan 'machines' harus list")
    if zone and len(zone) < 3:
        raise HTTPException(status_code=400,
                            detail="Zona butuh minimal 3 titik")

    cal = calibration.set(line_id, zone, machines)
    await manager.broadcast(snapshot())
    return {"status": "ok", **cal}


@router.delete("/api/lines/{line_id}/calibration")
async def api_calibration_delete(line_id: str):
    if not calibration.delete(line_id):
        raise HTTPException(status_code=404, detail="Belum dikalibrasi")
    log.warning("Kalibrasi %s dihapus", line_id)
    await manager.broadcast(snapshot())
    return {"status": "ok"}
