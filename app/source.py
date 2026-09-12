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

# Nama operator TIDAK dikarang di sini. Di mode simulasi kolomnya dibiarkan
# kosong; di produksi diisi dari roster/absensi lewat LiveSource.bootstrap().
# Nama karangan di layar lantai produksi mustahil dibedakan dari nama asli.

# Nama shift diambil dari ShiftSchedule (app/shifts.py), bukan dari sini.
SHIFTS = list(settings.SHIFT_NAMES)

STATUS_POOL = ["run", "run", "run", "run", "idle", "stop"]

# TODO(pabrik): jumlah line per jenis mesin HARUS disamakan dengan pabrik.
# Format: (kode, label, jumlah_line, hall). Kode dipakai sebagai awalan
# line_id ("ajl-01") dan HARUS sama dengan nama stream di go2rtc.
LAYOUT = [
    ("rapier", "Rapier", 6, "hall-a"),
    ("ajl", "AJL", 9, "hall-b"),
    ("shuttle", "Shuttle", 3, "hall-c"),
]

# TODO(pabrik): denah ini KARANGAN. Ukur tata letak asli lalu sesuaikan
# x/y/w/h (persen dari luas denah). Posisi tiap line dihitung otomatis
# oleh _grid_pos(); kalau susunan aslinya tidak berupa grid rapi, isi
# `pos` tiap line secara manual di LiveSource.bootstrap().
HALLS = [
    Hall(key="hall-a", label="Hall A — Rapier", x=4, y=8, w=44, h=40),
    Hall(key="hall-b", label="Hall B — AJL", x=53, y=8, w=43, h=84),
    Hall(key="hall-c", label="Hall C — Shuttle", x=4, y=56, w=44, h=36),
]
HALL_BY_KEY = {h.key: h for h in HALLS}

# Simulator TIDAK LAGI mengarang alert. Satu-satunya sumber alert adalah
# AI worker lewat POST /api/alerts.
#
# Alasannya bukan sekadar kerapian tampilan: tiap alert yang muncul juga
# ditulis ke tabel `alerts` di data/history.db, lengkap dengan waktu tanggap
# dan siapa yang menutupnya. Alert karangan mencemari jejak audit dan membuat
# statistik false-alarm serta rata-rata waktu tanggap dihitung dari kejadian
# yang tidak pernah terjadi — dan setelah tercampur, tidak ada cara
# membedakannya dari alert sungguhan.


def _teks(v, maks: int) -> str:
    """Teks dari payload luar: dipotong, tanpa baris baru & karakter kendali.

    Panjangnya dibatasi karena nilai ini ikut disiarkan ke semua browser
    pada setiap snapshot, dan disimpan ke basis data jejak audit.
    """
    if v is None:
        return ""
    s = str(v)[:maks]
    return "".join(c for c in s if c == " " or c.isprintable()).strip()


def _angka(v, low: int, high: int) -> int:
    """Bilangan bulat dari payload luar, dijepit ke rentang yang masuk akal."""
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return low
    return max(low, min(high, n))


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
        self.period_key: str = ""        # diisi oleh app/main.py saat startup

    # ---------- reset per shift / per hari ----------
    def reset_counters(self, period_key: str) -> None:
        """Nolkan penghitung produksi saat periode berganti.

        `output` dan `stops` bersifat per-periode. Tanpa ini angkanya
        menumpuk selamanya dan "Output Hari Ini" jadi tidak masuk akal.
        Nilai sebelum reset sudah tersimpan di database histori.
        """
        for line in self.all_lines():
            line.output = 0
            line.stops = 0
            for m in line.machines:
                m.output = 0
                m.stops = 0
                m.downtime = 0
            rollup(line)
        self.period_key = period_key

    def apply_shift(self, shift_name: str) -> None:
        """Perbarui nama shift di semua line."""
        for line in self.all_lines():
            line.shift = shift_name

    def export_counters(self) -> dict:
        """Penghitung berjalan, untuk disimpan supaya tahan restart."""
        return {
            "period_key": self.period_key,
            "lines": {
                l.id: {
                    "output": l.output,
                    "stops": l.stops,
                    "machines": {str(m.no): {"output": m.output,
                                             "stops": m.stops,
                                             "downtime": m.downtime}
                                 for m in l.machines},
                } for l in self.all_lines()
            },
        }

    def import_counters(self, data: dict) -> int:
        """Kembalikan penghitung setelah restart. Return jumlah line dipulihkan."""
        lines = (data or {}).get("lines") or {}
        n = 0
        for line in self.all_lines():
            saved = lines.get(line.id)
            if not saved:
                continue
            line.output = int(saved.get("output", 0))
            line.stops = int(saved.get("stops", 0))
            for m in line.machines:
                sm = (saved.get("machines") or {}).get(str(m.no))
                if sm:
                    m.output = int(sm.get("output", 0))
                    m.stops = int(sm.get("stops", 0))
                    m.downtime = int(sm.get("downtime", 0))
            rollup(line)
            n += 1
        self.period_key = (data or {}).get("period_key", self.period_key)
        return n

    def vision_active(self, line_id: str) -> bool:
        """True kalau line ini sedang disuplai data asli dari AI worker."""
        return self._vision_until.get(line_id, 0) > time.time()

    def vision_age(self, line_id: str) -> Optional[int]:
        """Detik sejak data kamera terakhir. None = belum pernah menerima.

        Dipakai dashboard untuk membedakan "AI tidak menemukan apa-apa" dari
        "AI sudah berhenti mengirim". Keduanya terlihat sama di layar, tetapi
        yang kedua berarti line ini tidak terpantau sama sekali.
        """
        until = self._vision_until.get(line_id)
        if not until:
            return None
        return max(0, int(time.time() - (until - self.VISION_LEASE)))

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

        Isi payload TIDAK dipercaya. Alert disiarkan ke layar setiap
        operator tiap beberapa detik, jadi nilai yang cacat di sini tidak
        berhenti di satu permintaan: teks sepanjang berpuluh megabyte akan
        dikirim berulang ke semua browser, dan angka yang bukan bilangan
        akan menjatuhkan endpoint-nya.
        """
        line = self.get_line(line_id)
        if line is None:
            return False
        now = datetime.now().strftime("%H:%M:%S")
        alert_id = _teks(payload.get("id"), 64) or "ALR-{}-{}".format(
            line_id, datetime.now().strftime("%H%M%S"))
        severity = payload.get("severity")
        line.alert = Alert(
            id=alert_id,
            line_id=line_id,
            label=_teks(payload.get("label"), 120) or "Deteksi",
            confidence=_angka(payload.get("confidence"), 0, 100),
            zone=_teks(payload.get("zone"), 80) or line.name,
            activity=_teks(payload.get("activity"), 200),
            object_type=_teks(payload.get("object_type"), 40) or "Person",
            duration=_angka(payload.get("duration"), 0, 86400),
            detected_at=_teks(payload.get("detected_at"), 32) or now,
            # hanya dua nilai yang dikenali tampilan; apa pun selain "med"
            # diperlakukan sebagai "high" supaya tidak ada alert yang
            # kehilangan penandaan gara-gara salah ketik
            severity="med" if severity == "med" else "high",
            snapshots=[_teks(t, 32) for t in
                       (payload.get("snapshots") or [now])[:12]],
        )
        # Alert tidak kedaluwarsa sendiri — hanya ditutup operator, supaya
        # selalu ada jejak siapa yang menanganinya.
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
                    operator="",        # tidak dikarang — lihat catatan di atas
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

    def refresh(self) -> None:
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


class LiveSource(BaseSource):
    """Adapter ke sistem pabrik — dipakai kalau CCTV_SOURCE=live.

    ================================================================
    TODO(pabrik): INI YANG WAJIB DIISI SEBELUM GO-LIVE.
    Selama kelas ini kosong, dashboard hanya bisa jalan dengan data
    simulasi dan SEMUA angka produksinya karangan.
    ================================================================

    Yang perlu diisi hanya dua method. Struktur data (Line, Machine,
    Camera, Position) tidak perlu diubah, dan frontend ikut otomatis.
    """

    def bootstrap(self) -> None:
        """Dipanggil SEKALI saat startup: bangun daftar line & mesin.

        TODO(pabrik): ganti isi method ini dengan pembacaan master data.
        Kerangka lengkapnya:

            for row in db.query("SELECT line_id, nama, jenis, hall "
                                "FROM master_line ORDER BY line_id"):
                group = self._group(row.jenis)          # rapier/ajl/shuttle
                line = Line(
                    id=row.line_id,                     # HARUS sama dengan
                                                        # nama stream go2rtc
                    name=row.nama,
                    type=row.jenis,
                    area=row.hall,
                    mesin=row.jumlah_mesin,
                    cam=Camera(id="CAM-" + row.line_id.upper(),
                               online=True,             # dari status NVR
                               stream=_stream_url(row.line_id),
                               stream_grid=_stream_url(row.line_id, grid=True)),
                    pos=Position(x=row.denah_x, y=row.denah_y,
                                 w=row.denah_w, h=row.denah_h),
                    machines=[Machine(no=m.no, name=m.kode)
                              for m in db.mesin_of(row.line_id)],
                )
                group.lines.append(line)

        Sumber yang umum dipakai: database loom monitoring, tag OPC-UA /
        Modbus dari PLC, atau REST API vendor mesin.
        """
        raise NotImplementedError(
            "LiveSource.bootstrap() belum diisi — lihat TODO(pabrik) di "
            "app/source.py. Set CCTV_SOURCE=sim untuk sementara."
        )

    def refresh(self) -> None:
        """Dipanggil tiap CCTV_PUSH_INTERVAL detik: perbarui nilai runtime.

        TODO(pabrik): isi status/rpm/eff/output/stops TIAP MESIN dari
        sumber real, lalu panggil rollup(line) supaya nilai line ikut.

            for line in self.all_lines():
                if self.vision_active(line.id):
                    continue          # status dari kamera, jangan ditimpa
                for m in line.machines:
                    d = plc.read(m.name)
                    m.status = d.status         # run | idle | stop | off
                    m.rpm    = d.rpm
                    m.eff    = d.efisiensi
                    m.output = d.output_periode  # HARUS per-periode, bukan
                                                 # akumulasi sejak mesin dipasang
                    m.stops  = d.jumlah_stop
                    m.order_mo = d.mo           # dari PPIC/ERP
                rollup(line)

        PENTING soal `output`: nilai yang dikirim harus akumulasi SEJAK
        AWAL PERIODE (shift/hari), bukan sejak mesin dipasang. Reset
        periode di dashboard (reset_counters) menolkan penghitungnya —
        kalau sumber tetap mengirim akumulasi total, angkanya akan
        melompat kembali setelah reset.

        TODO(pabrik): pastikan juga `cam.online` diperbarui dari status
        NVR. Kamera mati diam-diam adalah kegagalan paling berbahaya:
        dashboard terlihat normal padahal buta.
        """
        raise NotImplementedError

    def _group(self, key: str) -> Group:
        """Ambil (atau buat) grup untuk jenis mesin tertentu."""
        for g in self.groups:
            if g.key == key:
                return g
        g = Group(key=key, label=key.upper())
        self.groups.append(g)
        return g


def build_source() -> BaseSource:
    src = LiveSource() if settings.SOURCE == "live" else SimSource()
    src.bootstrap()
    return src
