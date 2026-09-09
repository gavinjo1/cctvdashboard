"""CCTV Monitoring Dashboard — FastAPI app.

Jalankan:  python run.py
Atau    :  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import List

from fastapi import Body, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .calibration import CalibrationStore
from .config import settings
from .source import build_source

BASE_DIR = Path(__file__).resolve().parent.parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s %(message)s",
)
log = logging.getLogger("cctv")

app = FastAPI(title="CCTV Monitoring Dashboard", docs_url="/api/docs")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

source = build_source()
calibration = CalibrationStore(settings.CALIBRATION_FILE)


# --------------------------------------------------------------------------
# Manajemen koneksi WebSocket
# --------------------------------------------------------------------------
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


def _line_with_cal(group) -> dict:
    """Sertakan kalibrasi (zona & ROI lampu) di tiap line."""
    d = group.to_dict()
    for line in d["lines"]:
        line["cal"] = calibration.get(line["id"]) or {"zone": [], "machines": []}
    return d


def snapshot() -> dict:
    """Payload lengkap: struktur group + nilai runtime + ringkasan KPI."""
    return {
        "type": "snapshot",
        "plant": settings.PLANT_NAME,
        "stream_mode": settings.STREAM_MODE,
        "stream_img_refresh": settings.STREAM_IMG_REFRESH,
        "groups": [_line_with_cal(g) for g in source.groups],
        "halls": source.halls_dict(),
        "summary": source.summary(),
    }


# --------------------------------------------------------------------------
# Background task: refresh data lalu broadcast
# --------------------------------------------------------------------------
async def push_loop() -> None:
    while True:
        try:
            await asyncio.sleep(settings.PUSH_INTERVAL)
            source.refresh()
            await manager.broadcast(snapshot())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("push_loop error — lanjut ke siklus berikutnya")


@app.on_event("startup")
async def on_startup() -> None:
    log.info(
        "Sumber data: %s | %d line | push tiap %ds",
        settings.SOURCE, len(source.all_lines()), settings.PUSH_INTERVAL,
    )
    app.state.pusher = asyncio.create_task(push_loop())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    task = getattr(app.state, "pusher", None)
    if task:
        task.cancel()


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "plant": settings.PLANT_NAME,
            "mesin_per_line": settings.MESIN_PER_LINE,
            "target_output": settings.TARGET_OUTPUT,
        },
    )


@app.get("/api/lines")
async def api_lines():
    return JSONResponse(snapshot())


@app.get("/api/lines/{line_id}")
async def api_line(line_id: str):
    line = source.get_line(line_id)
    if line is None:
        raise HTTPException(status_code=404, detail="Line tidak ditemukan")
    return JSONResponse(line.to_dict())


@app.get("/api/summary")
async def api_summary():
    return JSONResponse(source.summary())


@app.get("/api/alerts")
async def api_alerts():
    """Daftar alert AI yang sedang aktif."""
    from dataclasses import asdict
    return JSONResponse([asdict(a) for a in source.alerts()])


@app.post("/api/alerts")
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
    await manager.broadcast(snapshot())
    return {"status": "ok"}


@app.post("/api/alerts/{alert_id}/resolve")
async def api_resolve_alert(alert_id: str, payload: dict = Body(default={})):
    """Operator menekan Tindak Lanjuti / False Alarm di dashboard."""
    action = payload.get("action", "accept")
    if action not in ("accept", "false"):
        raise HTTPException(status_code=400, detail="action: accept | false")
    if not source.resolve_alert(alert_id, action):
        raise HTTPException(status_code=404, detail="Alert tidak ditemukan")
    log.info("Alert %s ditutup (%s)", alert_id, action)
    await manager.broadcast(snapshot())
    return {"status": "ok", "action": action}


@app.post("/api/lines/{line_id}/machines")
async def api_machine_status(line_id: str, payload: dict = Body(...)):
    """Status mesin hasil pembacaan lampu tower oleh AI worker.

    Body: {"machines": [{"no": 1, "status": "run", "color": "green"}, ...]}
    """
    machines = payload.get("machines")
    if not isinstance(machines, list):
        raise HTTPException(status_code=400, detail="field 'machines' wajib berupa list")
    n = source.apply_machine_status(line_id, machines)
    if n < 0:
        raise HTTPException(status_code=404, detail="Line tidak ditemukan")
    await manager.broadcast(snapshot())
    return {"status": "ok", "updated": n}


@app.get("/api/calibration")
async def api_calibration_all():
    """Semua kalibrasi — dipakai AI worker saat start."""
    return JSONResponse(calibration.all())


@app.get("/api/lines/{line_id}/calibration")
async def api_calibration_get(line_id: str):
    cal = calibration.get(line_id)
    if cal is None:
        raise HTTPException(status_code=404, detail="Belum dikalibrasi")
    return JSONResponse(cal)


@app.put("/api/lines/{line_id}/calibration")
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


@app.delete("/api/lines/{line_id}/calibration")
async def api_calibration_delete(line_id: str):
    if not calibration.delete(line_id):
        raise HTTPException(status_code=404, detail="Belum dikalibrasi")
    await manager.broadcast(snapshot())
    return {"status": "ok"}


@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "lines": len(source.all_lines()),
        "alerts": len(source.alerts()),
        "calibrated": len(calibration.all()),
        "source": settings.SOURCE,
    }


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        await ws.send_text(json.dumps(snapshot()))   # kirim state awal
        while True:
            await ws.receive_text()                  # keep-alive dari klien
    except WebSocketDisconnect:
        await manager.disconnect(ws)
    except Exception:
        await manager.disconnect(ws)
