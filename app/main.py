"""Dashboard CCTV + produksi pabrik weaving — perakitan aplikasi.

Berkas ini MERAKIT, bukan mengerjakan sendiri. Isinya hanya: membuat
aplikasi FastAPI, memasang router, menyalakan dan mematikan tugas latar,
dan dua endpoint yang tidak masuk kelompok mana pun (halaman depan dan
pemeriksaan kesehatan).

    app/state.py    objek yang hidup selama aplikasi jalan + bentuk snapshot
    app/latar.py    tugas latar: simpan riwayat, dorong snapshot, ganti shift
    app/rute/       endpoint HTTP, satu berkas per urusan

Dulu semuanya di sini: 26 endpoint, dua loop latar, dan seluruh state dalam
satu berkas 627 baris.
"""
import asyncio
import json
import logging

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import settings
from .latar import boot_state, history_loop, push_loop
from .rute import alert, kalibrasi, laporan, line, log as rute_log, mo
from .state import (BASE_DIR, asset_version, calibration, history, manager,
                    schedule, snapshot, source)

log = logging.getLogger("cctv")

app = FastAPI(title="CCTV Monitoring Dashboard", docs_url="/api/docs")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")),
          name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

for r in (line.router, alert.router, kalibrasi.router, mo.router,
          laporan.router, rute_log.router):
    app.include_router(r)


# --------------------------------------------------------------------------
# Siklus hidup
# --------------------------------------------------------------------------
@app.on_event("startup")
async def on_startup() -> None:
    log.info("Sumber data: %s | %d line | push tiap %ds",
             settings.SOURCE, len(source.all_lines()), settings.PUSH_INTERVAL)
    log.info("Shift: %s | reset tiap %s",
             schedule.info()["name"], settings.RESET_MODE)

    warnings = settings.production_warnings()
    if warnings:
        log.warning("=" * 66)
        log.warning("BELUM SIAP PRODUKSI — %d hal perlu diperbaiki:",
                    len(warnings))
        for w in warnings:
            log.warning("  * %s", w)
        log.warning("Lihat PRODUCTION.md dan cari TODO(pabrik) di kode.")
        log.warning("=" * 66)

    boot_state()
    app.state.pusher = asyncio.create_task(push_loop())
    app.state.keeper = asyncio.create_task(history_loop())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    for name in ("pusher", "keeper"):
        task = getattr(app.state, name, None)
        if task:
            task.cancel()
    # simpan sekali lagi supaya angka tidak mundur setelah restart
    try:
        history.save_state("counters", source.export_counters())
        history.close()
    except Exception:
        log.exception("Gagal menyimpan state saat shutdown")


# --------------------------------------------------------------------------
# Halaman depan & kesehatan
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
            "v": asset_version(),
        },
    )


@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "lines": len(source.all_lines()),
        "alerts": len(source.alerts()),
        "calibrated": len(calibration.all()),
        "source": settings.SOURCE,
        "shift": schedule.info()["name"],
        "period": source.period_key,
        "production_ready": not settings.production_warnings(),
        "warnings": settings.production_warnings(),
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
