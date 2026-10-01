"""Alert: masuk dari AI worker, ditutup oleh operator."""
import logging
from dataclasses import asdict

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import JSONResponse

from ..auth import operators, require_api_key
from ..state import history, manager, snapshot, source

log = logging.getLogger("cctv")
router = APIRouter()


@router.get("/api/alerts")
async def api_alerts():
    """Daftar alert AI yang sedang aktif."""
    return JSONResponse([asdict(a) for a in source.alerts()])


@router.post("/api/alerts", dependencies=[Depends(require_api_key)])
async def api_push_alert(payload: dict = Body(...)):
    """Dipanggil AI worker saat kamera mendeteksi sesuatu.

    Body: {"line_id": "ajl-01", "label": "...", "confidence": 92,
           "activity": "...", "object_type": "Person", "duration": 11}
    """
    line_id = payload.get("line_id")
    if not line_id:
        raise HTTPException(status_code=400, detail="line_id wajib diisi")
    if not source.push_alert(line_id, payload):
        raise HTTPException(status_code=404, detail="Line tidak ditemukan")

    line = source.get_line(line_id)
    if line and line.alert:
        history.log_alert(line.alert)          # jejak audit
    await manager.broadcast(snapshot())
    return {"status": "ok"}


@router.post("/api/alerts/{alert_id}/resolve")
async def api_resolve_alert(alert_id: str, payload: dict = Body(default={})):
    """Operator menekan Tindak Lanjuti / False Alarm di dashboard."""
    action = payload.get("action", "accept")
    if action not in ("accept", "false"):
        raise HTTPException(status_code=400, detail="action: accept | false")

    # Siapa yang menutup alert — inti jejak audit. Tanpa ini tidak ada
    # cara membuktikan mesin stop sudah ditangani atau belum.
    by = (payload.get("by") or "").strip()
    if not operators.valid(by):
        raise HTTPException(
            status_code=400,
            detail="Nama operator wajib diisi dan harus ada di daftar operator")

    if not source.resolve_alert(alert_id, action):
        raise HTTPException(status_code=404, detail="Alert tidak ditemukan")

    history.resolve_alert(alert_id, action, by)
    log.info("Alert %s ditutup oleh %s (%s)", alert_id, by, action)
    await manager.broadcast(snapshot())
    return {"status": "ok", "action": action, "by": by}


@router.get("/api/operators")
async def api_operators():
    """Daftar nama yang boleh menutup alert (untuk dropdown di dashboard)."""
    return {"names": operators.all(), "free_text": not operators.all()}
