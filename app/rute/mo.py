"""Penugasan MO per mesin, diisi PPIC lewat portal MO."""
import logging

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse

from ..mo import bersihkan_mo
from ..source import rollup
from ..state import manager, mo_store, snapshot, source

log = logging.getLogger("cctv")
router = APIRouter()


@router.get("/api/mo")
async def api_mo_all():
    """Semua penugasan MO. Dipakai portal MO saat dibuka."""
    return JSONResponse(mo_store.all())


@router.put("/api/lines/{line_id}/mo")
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
