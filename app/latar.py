"""Tugas latar: simpan riwayat, dorong snapshot, tangani pergantian shift.

Dipisah dari main.py karena ini satu-satunya bagian yang berjalan SENDIRI
tanpa ada yang memanggil lewat HTTP. Kalau tercampur dengan route, mudah
tertukar antara "apa yang terjadi saat orang membuka halaman" dan "apa yang
terjadi setiap lima detik selamanya".

Kedua loop di sini MENELAN kesalahan per siklus dengan sengaja: satu
kegagalan menyimpan tidak boleh menghentikan loopnya. Loop yang mati diam-
diam berarti dashboard berhenti diperbarui tanpa ada yang tahu.
"""
import asyncio
import logging
from datetime import datetime

from .config import settings
from .state import (accumulator, history, manager, resetter, schedule,
                    snapshot, source)

log = logging.getLogger("cctv")


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


def check_period() -> bool:
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
            check_period()
            source.refresh()
            accumulator.sample(source.all_lines())
            await manager.broadcast(snapshot())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("push_loop error — lanjut ke siklus berikutnya")


def boot_state() -> None:
    """Pulihkan penghitung dari database, atau reset kalau periode sudah ganti."""
    now = datetime.now()
    key = schedule.key_at(now)
    source.period_key = key
    source.apply_shift(schedule.name_at(now))

    saved = history.load_state("counters")
    if saved and saved.get("period_key") == key:
        n = source.import_counters(saved)
        log.info("Penghitung dipulihkan dari database: %d line (periode %s)",
                 n, key)
    elif saved:
        log.info("Periode sudah berganti (%s -> %s) — penghitung dimulai dari nol",
                 saved.get("period_key"), key)
        source.reset_counters(key)
    else:
        log.info("Belum ada state tersimpan — mulai dari nol (periode %s)", key)
