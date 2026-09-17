"""CCTV Monitoring Dashboard — FastAPI app.

Jalankan:  python run.py
Atau    :  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import (Body, Depends, FastAPI, HTTPException, Request,
                     WebSocket, WebSocketDisconnect)
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .auth import operators, require_api_key
from .calibration import CalibrationStore
from .mo import MoStore, bersihkan_mo
from .config import settings
from .eventlog import EpisodeTracker, buat_teks
from .report import PeriodAccumulator, build_report
from .shifts import ResetTracker, ShiftSchedule
from .source import build_source, rollup, _angka
from .store import HistoryStore

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


# --------------------------------------------------------------------------
# Background task: refresh data lalu broadcast
# --------------------------------------------------------------------------
async def history_loop() -> None:
    """Simpan snapshot produksi & penghitung secara berkala.

    Ini yang membuat data selamat dari restart dan tersedia untuk laporan.
    """
    purge_at = 0.0
    while True:
        try:
            await asyncio.sleep(settings.HISTORY_INTERVAL)
            info = schedule.info()
            history.save_production(source.all_lines(),
                                    info["period_key"], info["name"])
            history.save_state("counters", source.export_counters())

            # bersihkan histori lama sekali sehari
            now = asyncio.get_event_loop().time()
            if now - purge_at > 86400:
                purge_at = now
                history.purge(settings.HISTORY_RETENTION_DAYS)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("history_loop error — lanjut ke siklus berikutnya")


def _check_period() -> bool:
    """Reset penghitung kalau shift/hari sudah berganti. True kalau reset."""
    now = datetime.now()
    due, old, new = resetter.due(now)
    if not due:
        source.apply_shift(schedule.name_at(now))
        return False

    info = schedule.info(now)
    # simpan angka terakhir periode lama SEBELUM dinolkan.
    # Pakai nama shift periode LAMA, bukan yang sedang berjalan.
    old_name = schedule.name_of_key(old)
    try:
        history.save_production(source.all_lines(), old, old_name)
    except Exception:
        log.exception("Gagal menyimpan produksi periode %s sebelum reset", old)

    # tulis ringkasan periode yang baru berakhir SEBELUM akumulator dinolkan
    try:
        rows = accumulator.rows()
        history.save_shift_report(rows)
    except Exception:
        log.exception("Gagal menyimpan laporan periode %s", old)

    source.reset_counters(new)
    source.apply_shift(info["name"])
    accumulator.reset(new, info["name"], schedule.started_at(now))
    history.save_state("counters", source.export_counters())
    log.info("Periode berganti: %s (%s) -> %s (%s). Penghitung dinolkan.",
             old, old_name, new, info["name"])
    return True


async def push_loop() -> None:
    while True:
        try:
            await asyncio.sleep(settings.PUSH_INTERVAL)
            _check_period()
            source.refresh()
            accumulator.sample(source.all_lines())
            await manager.broadcast(snapshot())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("push_loop error — lanjut ke siklus berikutnya")


def _boot_state() -> None:
    """Pulihkan penghitung dari database, atau reset kalau periode sudah ganti."""
    now = datetime.now()
    key = schedule.key_at(now)
    source.period_key = key
    source.apply_shift(schedule.name_at(now))

    saved = history.load_state("counters")
    if saved and saved.get("period_key") == key:
        n = source.import_counters(saved)
        log.info("Penghitung dipulihkan dari database: %d line (periode %s)", n, key)
    elif saved:
        log.info("Periode sudah berganti (%s -> %s) — penghitung dimulai dari nol",
                 saved.get("period_key"), key)
        source.reset_counters(key)
    else:
        log.info("Belum ada state tersimpan — mulai dari nol (periode %s)", key)


@app.on_event("startup")
async def on_startup() -> None:
    log.info("Sumber data: %s | %d line | push tiap %ds",
             settings.SOURCE, len(source.all_lines()), settings.PUSH_INTERVAL)
    log.info("Shift: %s | reset tiap %s",
             schedule.info()["name"], settings.RESET_MODE)

    warnings = settings.production_warnings()
    if warnings:
        log.warning("=" * 66)
        log.warning("BELUM SIAP PRODUKSI — %d hal perlu diperbaiki:", len(warnings))
        for w in warnings:
            log.warning("  * %s", w)
        log.warning("Lihat PRODUCTION.md dan cari TODO(pabrik) di kode.")
        log.warning("=" * 66)

    _boot_state()
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
            "v": asset_version(),
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


@app.post("/api/alerts", dependencies=[Depends(require_api_key)])
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


@app.post("/api/alerts/{alert_id}/resolve")
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


@app.post("/api/lines/{line_id}/machines",
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
        raise HTTPException(status_code=400, detail="field 'machines' wajib berupa list")
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
    log.warning("Kalibrasi %s dihapus", line_id)
    await manager.broadcast(snapshot())
    return {"status": "ok"}


@app.get("/api/mo")
async def api_mo_all():
    """Semua penugasan MO. Dipakai portal MO saat dibuka."""
    return JSONResponse(mo_store.all())


@app.put("/api/lines/{line_id}/mo")
async def api_mo_set(line_id: str, payload: dict = Body(...)):
    """Simpan penugasan MO satu line dari portal.

    Body: {"machines": {"1": "MO-5285", "2": "MO-5285", "7": ""}}

    Nilai kosong MELEPAS mesin itu dari penugasan — itu cara membatalkan
    salah isi. Mesin yang tidak disebut ikut terlepas: badan permintaan
    adalah keadaan LENGKAP line itu, bukan tambalan sebagian.
    """
    line = source.get_line(line_id)
    if line is None:
        raise HTTPException(status_code=404, detail="Line tidak ditemukan")

    mesin = payload.get("machines")
    if not isinstance(mesin, dict):
        raise HTTPException(status_code=400,
                            detail="'machines' harus objek {no: mo}")

    sah = {str(m.no) for m in line.machines}
    asing = [k for k in mesin if str(k) not in sah]
    if asing:
        raise HTTPException(
            status_code=400,
            detail="Nomor mesin di luar line ini: %s" % ", ".join(sorted(asing)))

    ditolak = [str(k) for k, v in mesin.items()
               if str(v or "").strip() and not bersihkan_mo(v)]
    if ditolak:
        raise HTTPException(
            status_code=400,
            detail="Nomor MO tidak sah di mesin %s (maksimal 32 karakter, "
                   "huruf/angka/spasi/. _ - /)" % ", ".join(sorted(ditolak)))

    isi = mo_store.set(line_id, mesin)
    mo_store.terapkan(line)
    rollup(line)
    await manager.broadcast(snapshot())
    log.info("MO line %s diperbarui: %d mesin ditugasi", line_id, len(isi))
    return {"status": "ok", "machines": isi}


@app.get("/api/operators")
async def api_operators():
    """Daftar nama yang boleh menutup alert (untuk dropdown di dashboard)."""
    return {"names": operators.all(), "free_text": not operators.all()}


@app.get("/api/history/production")
async def api_history_production(line_id: str = None, hours: int = 24):
    return JSONResponse(history.production_history(line_id, hours))


@app.get("/api/history/alerts")
async def api_history_alerts(line_id: str = None, days: int = 7):
    return JSONResponse(history.alert_history(line_id, days))


@app.get("/api/history/alert-stats")
async def api_alert_stats(days: int = 7):
    """Rekap alert: jumlah, persen false alarm, rata-rata waktu tanggap."""
    return JSONResponse(history.alert_stats(days))


@app.get("/api/shift")
async def api_shift():
    return JSONResponse(schedule.info())


@app.get("/api/report/periods")
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


@app.get("/api/report/shift")
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


@app.get("/api/shift/summary")
async def api_shift_summary(period_key: str = None):
    key = period_key or schedule.key_at(datetime.now())
    return JSONResponse({"period_key": key,
                         "lines": history.shift_summary(key)})


@app.get("/api/log/lines")
async def api_log_lines():
    """Line yang sudah punya catatan episode, untuk daftar unduhan."""
    rows = history.episode_lines()
    nama = {l.id: l.name for l in source.all_lines()}
    for r in rows:
        r["name"] = nama.get(r["line_id"], r["line_id"])
        r["berjalan"] = len(tracker.running(r["line_id"]))
    return JSONResponse({"mode": settings.EVENT_LOG, "lines": rows})


@app.get("/api/log/{line_id}.txt", response_class=PlainTextResponse)
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


@app.delete("/api/log/{line_id}")
async def api_log_clear(line_id: str):
    """Kosongkan catatan satu line, untuk memulai sesi pengujian baru."""
    n = history.clear_episodes(line_id)
    tracker.reset(line_id)
    log.info("Catatan episode %s dikosongkan: %d baris", line_id, n)
    return {"status": "ok", "dihapus": n}


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
