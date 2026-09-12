"""Pengumpul nilai sepanjang periode untuk laporan shift.

MASALAH YANG DISELESAIKAN MODUL INI
-----------------------------------
`output` dan `stops` bersifat akumulatif, jadi nilai akhir periode sudah
benar. `eff`, `rpm`, dan `status` TIDAK — ketiganya adalah keadaan sesaat.
Mengambil cuplikan terakhir berarti melaporkan keadaan pada detik shift
berakhir, bukan sepanjang shift: satu mesin yang berhenti lima menit
sebelum pergantian akan tercatat 0% untuk seluruh shift.

Karena itu nilai sesaat dikumpulkan sepanjang periode dengan pembobotan
waktu, lalu ditulis satu baris ringkasan per mesin saat periode berakhir.

Cara ini juga hemat: 180 mesin x 1 baris per shift = 540 baris/hari,
dibanding 51.840 baris/hari bila tiap mesin dicuplik tiap 5 menit.
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("cctv")

STATUSES = ("run", "idle", "stop", "off")


@dataclass
class Bucket:
    """Akumulator satu mesin atau satu line sepanjang periode."""
    sec: Dict[str, float] = field(
        default_factory=lambda: {s: 0.0 for s in STATUSES})
    eff_sec: float = 0.0        # efisiensi x detik, seluruh waktu
    eff_run_sec: float = 0.0    # efisiensi x detik, hanya saat beroperasi
    rpm_run_sec: float = 0.0    # rpm x detik, hanya saat beroperasi
    output: int = 0             # nilai akhir (akumulatif)
    stops: int = 0
    downtime: int = 0
    mesin_run_sec: float = 0.0  # khusus line: jumlah mesin jalan x detik

    @property
    def total_sec(self) -> float:
        return sum(self.sec.values())

    def add(self, status: str, eff: float, rpm: float, dt: float) -> None:
        if status not in self.sec:
            status = "off"
        self.sec[status] += dt
        self.eff_sec += eff * dt
        if status == "run":
            self.eff_run_sec += eff * dt
            self.rpm_run_sec += rpm * dt

    def summary(self) -> dict:
        total = self.total_sec or 1.0
        run = self.sec["run"] or 1.0
        return {
            # rata-rata efisiensi sepanjang periode, mesin berhenti dihitung 0
            "eff_avg": round(self.eff_sec / total),
            # rata-rata efisiensi HANYA saat mesin beroperasi
            "eff_run": round(self.eff_run_sec / run) if self.sec["run"] else 0,
            "rpm_avg": round(self.rpm_run_sec / run) if self.sec["run"] else 0,
            # ketersediaan: porsi waktu mesin benar-benar beroperasi
            "availability": round(self.sec["run"] / total * 100),
            "sec_run": round(self.sec["run"]),
            "sec_idle": round(self.sec["idle"]),
            "sec_stop": round(self.sec["stop"]),
            "sec_off": round(self.sec["off"]),
            "sec_total": round(self.total_sec),
            "output": self.output,
            "stops": self.stops,
            "downtime": self.downtime,
        }


class PeriodAccumulator:
    """Mengumpulkan nilai seluruh line & mesin sepanjang satu periode."""

    def __init__(self, period_key: str, shift: str,
                 started_at: Optional[datetime] = None):
        self.reset(period_key, shift, started_at)

    def reset(self, period_key: str, shift: str,
              started_at: Optional[datetime] = None) -> None:
        self.period_key = period_key
        self.shift = shift
        self.started_at = started_at or datetime.now()
        self.lines: Dict[str, Bucket] = {}
        self.machines: Dict[Tuple[str, int], Bucket] = {}
        self.meta: Dict[str, dict] = {}      # info line yang tidak berubah
        self._last_ts = time.monotonic()

    # ---------- pencuplikan ----------
    def sample(self, lines) -> None:
        """Dipanggil tiap siklus. Selisih waktu dihitung sendiri."""
        now = time.monotonic()
        dt = now - self._last_ts
        self._last_ts = now
        # lompatan waktu tidak wajar (sistem tertidur) diabaikan
        if dt <= 0 or dt > 300:
            return

        for line in lines:
            lb = self.lines.setdefault(line.id, Bucket())
            lb.add(line.status, line.eff, line.rpm, dt)
            lb.output = line.output
            lb.stops = line.stops
            lb.mesin_run_sec += line.mesin_run * dt
            self.meta[line.id] = {
                "name": line.name, "type": line.type, "area": line.area,
                "operator": line.operator, "mesin": line.mesin,
            }
            for m in line.machines:
                mb = self.machines.setdefault((line.id, m.no), Bucket())
                mb.add(m.status, m.eff, m.rpm, dt)
                mb.output = m.output
                mb.stops = m.stops
                mb.downtime = m.downtime

    # ---------- keluaran ----------
    def rows(self) -> List[dict]:
        """Baris siap simpan: satu per line (machine_no=NULL) + satu per mesin."""
        ended = datetime.now().isoformat(timespec="seconds")
        started = self.started_at.isoformat(timespec="seconds")
        out = []

        for line_id, b in self.lines.items():
            meta = self.meta.get(line_id, {})
            s = b.summary()
            total = b.total_sec or 1.0
            out.append({
                "period_key": self.period_key, "shift": self.shift,
                "started_at": started, "ended_at": ended,
                "line_id": line_id, "machine_no": None,
                "name": meta.get("name", line_id),
                "operator": meta.get("operator", ""),
                "mesin_avg": round(b.mesin_run_sec / total, 1),
                **s,
            })

        for (line_id, no), b in self.machines.items():
            meta = self.meta.get(line_id, {})
            out.append({
                "period_key": self.period_key, "shift": self.shift,
                "started_at": started, "ended_at": ended,
                "line_id": line_id, "machine_no": no,
                "name": "%s-%s" % (meta.get("type", line_id).upper(),
                                   str(no).zfill(2)),
                "operator": meta.get("operator", ""),
                "mesin_avg": None,
                **b.summary(),
            })
        return out

    def live_report(self) -> dict:
        """Laporan periode yang SEDANG berjalan, tanpa menunggu shift selesai."""
        rows = self.rows()
        return build_report(self.period_key, self.shift, rows, live=True)


def build_report(period_key: str, shift: str, rows: List[dict],
                 alerts: Optional[dict] = None, live: bool = False) -> dict:
    """Susun laporan dari baris ringkasan."""
    lines = [r for r in rows if r.get("machine_no") is None]
    machines = [r for r in rows if r.get("machine_no") is not None]

    total_output = sum(r["output"] for r in lines)
    total_stops = sum(r["stops"] for r in lines)
    sec_total = sum(r["sec_total"] for r in lines) or 1

    # Berapa lama data BENAR-BENAR tercuplik, dibanding rentang shift.
    # Dashboard yang baru dinyalakan di tengah shift hanya mencuplik
    # sebagian; laporan harus mengatakannya, bukan diam-diam terlihat
    # seolah mencakup satu shift penuh.
    sec_dipantau = sec_total / max(1, len(lines))
    sec_shift = sec_dipantau
    try:
        t0 = datetime.fromisoformat(lines[0]["started_at"])
        t1 = datetime.fromisoformat(lines[0]["ended_at"])
        sec_shift = max(1.0, (t1 - t0).total_seconds())
    except (ValueError, KeyError, IndexError):
        pass
    cakupan = min(100, round(sec_dipantau / sec_shift * 100))

    # efisiensi pabrik dibobot waktu, bukan rata-rata dari rata-rata
    eff_avg = round(sum(r["eff_avg"] * r["sec_total"] for r in lines) / sec_total)
    avail = round(sum(r["sec_run"] for r in lines) / sec_total * 100)

    worst = sorted(machines, key=lambda r: r["eff_avg"])[:10]
    most_stops = sorted(machines, key=lambda r: -r["stops"])[:10]

    return {
        "period_key": period_key,
        "shift": shift,
        "live": live,
        "started_at": lines[0]["started_at"] if lines else None,
        "ended_at": lines[0]["ended_at"] if lines else None,
        "ringkasan": {
            "line": len(lines),
            "mesin": len(machines),
            "output": total_output,
            "stops": total_stops,
            "eff_avg": eff_avg,
            "availability": avail,
            "durasi_menit": round(sec_dipantau / 60, 1),
            "durasi_shift_menit": round(sec_shift / 60),
            "cakupan": cakupan,
        },
        "lines": sorted(lines, key=lambda r: r["line_id"]),
        "machines": sorted(machines, key=lambda r: (r["line_id"], r["machine_no"])),
        "mesin_terburuk": worst,
        "mesin_paling_sering_stop": most_stops,
        "alerts": alerts or {},
    }
