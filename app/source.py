"""Sumber data line.

SimSource  : data simulasi, untuk demo / development.
LiveSource : template adapter ke sistem pabrik (DB loom monitoring, PLC, OPC-UA)
             dan ke AI worker (deteksi dari CCTV).
"""
import random
import time
from datetime import datetime
from typing import Dict, List, Optional

from .config import settings
from .models import Alert, Camera, Group, Hall, Line, Machine, Position

OPERATORS = [
    "Sutrisno", "Wahyudi", "Rina M.", "Budi S.", "Aris P.", "Nurul H.",
    "Dedi K.", "Slamet R.", "Yuni A.", "Ahmad F.", "Tari W.", "Joko L.",
]
SHIFTS = ["Shift 1", "Shift 2", "Shift 3"]

STATUS_POOL = ["run", "run", "run", "run", "idle", "stop"]

# jumlah line per jenis mesin + hall tempatnya
LAYOUT = [
    ("rapier", "Rapier", 6, "hall-a"),
    ("ajl", "AJL", 9, "hall-b"),
    ("shuttle", "Shuttle", 3, "hall-c"),
]

# denah pabrik — posisi dalam persen (0-100)
HALLS = [
    Hall(key="hall-a", label="Hall A — Rapier", x=4, y=8, w=44, h=40),
    Hall(key="hall-b", label="Hall B — AJL", x=53, y=8, w=43, h=84),
    Hall(key="hall-c", label="Hall C — Shuttle", x=4, y=56, w=44, h=36),
]
HALL_BY_KEY = {h.key: h for h in HALLS}

# jenis deteksi AI yang relevan di lantai produksi
DETECTIONS = [
    ("Operator tidak di area", "Operator meninggalkan line", "Person", "high"),
    ("Mesin stop tanpa penanganan", "Lampu alarm menyala > 5 menit", "Machine", "high"),
    ("Orang tidak dikenal", "Orang tanpa seragam di area line", "Person", "high"),
    ("APD tidak lengkap", "Operator tanpa masker / penutup kepala", "Person", "med"),
    ("Kerumunan di line", "3 orang atau lebih berkumpul", "Person", "med"),
]


def _stream_url(line_id: str, grid: bool = False) -> str:
    """Bangun URL stream dari template. {id} diganti id line."""
    tpl = settings.STREAM_URL_GRID if grid and settings.STREAM_URL_GRID \
        else settings.STREAM_URL
    if not tpl:
        # kompatibilitas dengan CCTV_STREAM_BASE versi lama
        if settings.STREAM_BASE:
            return "{}/{}".format(settings.STREAM_BASE.rstrip("/"), line_id)
        return ""
    return tpl.replace("{id}", line_id)


def _grid_pos(hall: Hall, index: int, total: int, cols: int = 3) -> Position:
    """Susun line dalam grid di dalam hall."""
    rows = (total + cols - 1) // cols
    pad_x, pad_y, gap = 3.0, 5.0, 1.6
    inner_w = hall.w - pad_x * 2
    inner_h = hall.h - pad_y - pad_x
    cell_w = (inner_w - gap * (cols - 1)) / cols
    cell_h = (inner_h - gap * (rows - 1)) / rows
    c, r = index % cols, index // cols
    return Position(
        x=round(hall.x + pad_x + c * (cell_w + gap), 2),
        y=round(hall.y + pad_y + r * (cell_h + gap), 2),
        w=round(cell_w, 2),
        h=round(cell_h, 2),
    )


def rollup(line: Line) -> None:
    """Nilai line = agregat dari mesin-mesinnya."""
    ms = line.machines
    if not ms:
        return
    line.mesin_run = sum(1 for m in ms if m.status == "run")
    runners = [m for m in ms if m.status == "run"]
    line.rpm = round(sum(m.rpm for m in runners) / len(runners)) if runners else 0
    line.eff = round(sum(m.eff for m in ms) / len(ms))
    line.output = sum(m.output for m in ms)
    line.stops = sum(m.stops for m in ms)
    line.mo_groups = group_by_mo(ms)


def group_by_mo(machines: List[Machine]) -> List[dict]:
    """Kelompokkan mesin berdasarkan MO.

    Hasil: [{"mo": "MO-1010", "machines": [1, 4, 5, 6], "count": 4,
             "running": 3, "output": 1240, "eff": 88}, ...]
    """
    buckets: Dict[str, List[Machine]] = {}
    for m in machines:
        buckets.setdefault(m.order_mo, []).append(m)

    out = []
    for mo, group in buckets.items():
        out.append({
            "mo": mo,
            "machines": [m.no for m in group],
            "count": len(group),
            "running": sum(1 for m in group if m.status == "run"),
            "output": sum(m.output for m in group),
            "eff": round(sum(m.eff for m in group) / len(group)) if group else 0,
            "stops": sum(m.stops for m in group),
        })
    # MO dengan mesin terbanyak di atas, lalu urut nomor MO
    out.sort(key=lambda g: (-g["count"], g["mo"]))
    return out


class BaseSource:
    #: berapa lama data dari AI worker dianggap masih berlaku (detik).
    #: Selama masih berlaku, simulator TIDAK menimpa line tersebut.
    VISION_LEASE = 60

    def __init__(self):
        self.groups: List[Group] = []
        self.halls: List[Hall] = HALLS
        self._vision_until: Dict[str, float] = {}

    def vision_active(self, line_id: str) -> bool:
        """True kalau line ini sedang disuplai data asli dari AI worker."""
        return self._vision_until.get(line_id, 0) > time.time()

    def bootstrap(self) -> None:
        raise NotImplementedError

    def refresh(self) -> None:
        raise NotImplementedError

    # ---------- akses ----------
    def all_lines(self) -> List[Line]:
        return [l for g in self.groups for l in g.lines]

    def get_line(self, line_id: str) -> Optional[Line]:
        for line in self.all_lines():
            if line.id == line_id:
                return line
        return None

    def alerts(self) -> List[Alert]:
        return [l.alert for l in self.all_lines() if l.alert]

    def push_alert(self, line_id: str, payload: Dict) -> bool:
        """Dipanggil AI worker lewat POST /api/alerts saat ada deteksi.

        Berlaku di mode sim maupun live, supaya AI worker bisa diuji
        sebelum adapter data pabrik selesai dibuat.
        """
        line = self.get_line(line_id)
        if line is None:
            return False
        now = datetime.now().strftime("%H:%M:%S")
        alert_id = payload.get("id") or "ALR-{}-{}".format(
            line_id, datetime.now().strftime("%H%M%S"))
        line.alert = Alert(
            id=alert_id,
            line_id=line_id,
            label=payload["label"],
            confidence=int(payload.get("confidence", 0)),
            zone=payload.get("zone", line.name),
            activity=payload.get("activity", ""),
            object_type=payload.get("object_type", "Person"),
            duration=int(payload.get("duration", 0)),
            detected_at=payload.get("detected_at") or now,
            severity=payload.get("severity", "high"),
            snapshots=payload.get("snapshots") or [now],
        )
        # alert dari AI tidak kedaluwarsa sendiri — hanya ditutup operator
        ttl = getattr(self, "_alert_ttl", None)
        if ttl is not None:
            ttl.pop(alert_id, None)
        return True

    def apply_machine_status(self, line_id: str, machines: List[Dict]) -> int:
        """Terapkan status mesin hasil pembacaan lampu tower oleh AI worker.

        machines: [{"no": 1, "status": "run"|"idle"|"stop"|"off",
                    "color": "green", ...}, ...]
        Return: jumlah mesin yang berhasil diperbarui.
        """
        line = self.get_line(line_id)
        if line is None:
            return -1
        by_no = {m.no: m for m in line.machines}
        n = 0
        for item in machines:
            m = by_no.get(int(item.get("no", 0)))
            if m is None:
                continue
            st = item.get("status")
            if st in ("run", "idle", "stop", "off"):
                if m.status == "run" and st != "run":
                    m.stops += 1          # transisi jalan -> berhenti
                m.status = st
                if st != "run":
                    m.rpm = 0
                n += 1
        # tandai line ini dikendalikan data asli, jangan ditimpa simulator
        self._vision_until[line_id] = time.time() + self.VISION_LEASE

        # status line mengikuti mayoritas mesinnya
        if line.machines:
            run = sum(1 for m in line.machines if m.status == "run")
            line.status = "run" if run > len(line.machines) / 2 else (
                "stop" if run == 0 else "idle")
        rollup(line)
        return n

    def resolve_alert(self, alert_id: str, action: str) -> bool:
        """action: 'accept' (ditindaklanjuti) atau 'false' (false alarm)."""
        for line in self.all_lines():
            if line.alert and line.alert.id == alert_id:
                getattr(self, "_alert_ttl", {}).pop(alert_id, None)
                line.alert = None
                return True
        return False

    # ---------- ringkasan ----------
    def summary(self) -> dict:
        lines = self.all_lines()
        if not lines:
            return {}
        total = len(lines)
        run = sum(1 for l in lines if l.status == "run")
        idle = sum(1 for l in lines if l.status == "idle")
        stop = sum(1 for l in lines if l.status == "stop")
        mesin_total = sum(l.mesin for l in lines)
        mesin_run = sum(l.mesin_run for l in lines)
        output = sum(l.output for l in lines)
        eff = round(sum(l.eff for l in lines) / total)
        return {
            "total_line": total,
            "run": run,
            "idle": idle,
            "stop": stop,
            "run_pct": round(run / total * 100),
            "mesin_total": mesin_total,
            "mesin_run": mesin_run,
            "mesin_pct": round(mesin_run / mesin_total * 100) if mesin_total else 0,
            "eff": eff,
            "output": output,
            "target": settings.TARGET_OUTPUT,
            "output_pct": round(output / settings.TARGET_OUTPUT * 100)
            if settings.TARGET_OUTPUT else 0,
            "mesin_per_line": settings.MESIN_PER_LINE,
            "alerts": len(self.alerts()),
        }

    def halls_dict(self) -> List[dict]:
        return [
            {"key": h.key, "label": h.label, "x": h.x, "y": h.y, "w": h.w, "h": h.h}
            for h in self.halls
        ]


class SimSource(BaseSource):
    """Data simulasi — dipakai kalau CCTV_SOURCE=sim."""

    def __init__(self):
        super().__init__()
        # sisa umur tiap alert (dalam siklus refresh) supaya tidak menumpuk
        self._alert_ttl: Dict[str, int] = {}

    def bootstrap(self) -> None:
        self.groups = []
        for key, label, count, hall_key in LAYOUT:
            hall = HALL_BY_KEY[hall_key]
            group = Group(key=key, label=label)
            for i in range(1, count + 1):
                num = str(i).zfill(2)
                line_id = "{}-{}".format(key, num)
                line = Line(
                    id=line_id,
                    name="{} {}".format(key.upper(), num),
                    type=key,
                    status=random.choice(STATUS_POOL),
                    operator=random.choice(OPERATORS),
                    shift=random.choice(SHIFTS),
                    mesin=settings.MESIN_PER_LINE,
                    stops=random.randint(0, 14),
                    area=hall.label,
                    cam=Camera(
                        id="CAM-{}{}".format(key.upper(), num),
                        online=random.random() > 0.06,
                        stream=_stream_url(line_id),
                        stream_grid=_stream_url(line_id, grid=True),
                    ),
                    pos=_grid_pos(hall, i - 1, count),
                    machines=[
                        Machine(no=m, name="{}-{}".format(key.upper(), str(m).zfill(2)))
                        for m in range(1, settings.MESIN_PER_LINE + 1)
                    ],
                )
                self._assign_mo(line)
                self._roll_machines(line)
                for m in line.machines:
                    m.output = random.randint(120, 480) if m.status == "run" \
                        else random.randint(0, 140)
                self._rollup(line)
                group.lines.append(line)
            self.groups.append(group)

    # ---------- helper ----------
    @staticmethod
    def _assign_mo(line: Line) -> None:
        """Tiap line mengerjakan 1-3 MO, dibagi acak ke 10 mesin."""
        n_mo = random.choice([1, 2, 2, 3, 3])
        codes = random.sample(range(1000, 9999), n_mo)
        mos = ["MO-{}".format(c) for c in codes]
        for m in line.machines:
            m.order_mo = random.choice(mos)
        # pastikan tiap MO dipakai minimal 1 mesin
        used = {m.order_mo for m in line.machines}
        for mo in mos:
            if mo not in used:
                random.choice(line.machines).order_mo = mo

    # ---------- helper ----------
    @staticmethod
    def _roll_machines(line: Line) -> None:
        """Tentukan status tiap mesin sesuai status line."""
        if line.status == "run":
            target_run = random.randint(7, line.mesin)
        elif line.status == "idle":
            target_run = random.randint(3, 6)
        else:
            target_run = random.randint(0, 2)

        idx = list(range(line.mesin))
        random.shuffle(idx)
        running = set(idx[:target_run])

        for i, m in enumerate(line.machines):
            if i in running:
                m.status = "run"
                m.rpm = random.randint(320, 560)
                m.eff = random.randint(78, 97)
                m.downtime = random.randint(0, 12)
            else:
                m.status = random.choice(["idle", "stop", "stop"])
                m.rpm = 0
                m.eff = random.randint(0, 55)
                m.downtime = random.randint(15, 180)

    _rollup = staticmethod(rollup)

    def _maybe_alert(self, line: Line) -> None:
        """Simulasi deteksi AI. Line stop lebih sering memicu alert."""
        if line.alert:
            return
        if not line.cam.online:
            return
        chance = 0.06 if line.status == "stop" else 0.012
        if random.random() > chance:
            return

        label, activity, obj, severity = random.choice(DETECTIONS)
        now = datetime.now()
        stamp = now.strftime("%H:%M:%S")
        prev = now.replace(second=max(0, now.second - 10)).strftime("%H:%M:%S")
        line.alert = Alert(
            id="ALR-{}-{}".format(line.id, now.strftime("%H%M%S")),
            line_id=line.id,
            label=label,
            confidence=random.randint(76, 97),
            zone=line.name,
            activity=activity,
            object_type=obj,
            duration=random.randint(6, 90),
            detected_at=stamp,
            severity=severity,
            snapshots=[stamp, prev],
        )
        self._alert_ttl[line.alert.id] = random.randint(4, 10)

    def _expire_alerts(self) -> None:
        """Alert hilang sendiri setelah beberapa siklus (anggap sudah ditangani)."""
        for line in self.all_lines():
            if not line.alert:
                continue
            if line.alert.id not in self._alert_ttl:
                continue        # alert dari AI worker: biarkan sampai ditutup operator
            left = self._alert_ttl[line.alert.id] - 1
            if left <= 0:
                self._alert_ttl.pop(line.alert.id, None)
                line.alert = None
            else:
                self._alert_ttl[line.alert.id] = left

    def refresh(self) -> None:
        self._expire_alerts()
        for line in self.all_lines():
            if self.vision_active(line.id):
                continue        # data asli dari AI worker — jangan disimulasi
            if random.random() < 0.15:
                line.status = random.choice(STATUS_POOL)
            self._roll_machines(line)
            for m in line.machines:
                if m.status == "run":
                    m.output += random.randint(0, 4)
                else:
                    m.stops += 1 if random.random() < 0.05 else 0
            self._rollup(line)
            self._maybe_alert(line)


class LiveSource(BaseSource):
    """Adapter ke sistem pabrik — dipakai kalau CCTV_SOURCE=live.

    Tiga hal yang perlu diisi:
      1. bootstrap()  : daftar line + mesin + posisi di denah + info kamera
      2. refresh()    : nilai runtime tiap mesin (dari PLC / DB loom monitoring)
      3. push_alert() : dipanggil AI worker saat kamera mendeteksi sesuatu
    """

    def bootstrap(self) -> None:
        # TODO: ambil master data line & mesin dari database pabrik.
        raise NotImplementedError(
            "LiveSource.bootstrap() belum diisi. "
            "Set CCTV_SOURCE=sim untuk sementara, atau isi adapter ini."
        )

    def refresh(self) -> None:
        # TODO: update status/rpm/eff/output tiap mesin dari sumber real.
        raise NotImplementedError


def build_source() -> BaseSource:
    src = LiveSource() if settings.SOURCE == "live" else SimSource()
    src.bootstrap()
    return src
