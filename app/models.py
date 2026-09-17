"""Struktur data line produksi, mesin, kamera, dan alert AI."""
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

STATUS_TEXT = {
    "run": "Running",
    "idle": "Idle / Setting",
    "stop": "Stop / Alarm",
    "off": "Offline",
    "unknown": "Belum terbaca",
}


@dataclass
class Camera:
    id: str
    online: bool = True
    stream: str = ""          # URL stream kualitas penuh (halaman detail)
    stream_grid: str = ""     # URL sub-stream resolusi rendah (grid kamera)


@dataclass
class Machine:
    """1 mesin di dalam line. Tiap mesin bisa mengerjakan MO berbeda."""
    no: int
    name: str
    order_mo: str = ""        # nomor MO yang sedang dikerjakan mesin ini
    # "unknown" = BELUM ADA PEMBACAAN LAMPU untuk mesin ini. Sengaja bukan
    # "run": mesin yang tidak diketahui keadaannya tidak boleh terlihat sehat.
    status: str = "unknown"
    color: str = ""           # warna lampu yang menyala ("merah"/"hijau"/...)
    vision: bool = False      # True kalau status ini datang dari baca lampu
    rpm: int = 0
    eff: int = 0              # persen
    output: int = 0           # meter
    stops: int = 0
    downtime: int = 0         # menit hari ini


@dataclass
class Alert:
    """Hasil deteksi AI dari kamera line."""
    id: str
    line_id: str
    label: str                # jenis deteksi
    confidence: int           # persen
    zone: str
    activity: str
    object_type: str
    duration: int             # detik
    detected_at: str          # HH:MM:SS
    severity: str = "high"    # high | med
    snapshots: List[str] = field(default_factory=list)   # daftar timestamp


@dataclass
class Position:
    """Posisi line di denah pabrik, dalam persen (0-100)."""
    x: float = 0
    y: float = 0
    w: float = 0
    h: float = 0


@dataclass
class Line:
    """1 line = 10 mesin = 1 operator = 1 kamera."""
    id: str
    name: str
    type: str                 # kode jenis mesin; pabrik ini semuanya "ajl"
    status: str = "run"
    operator: str = ""
    shift: str = ""
    mesin: int = 10
    mesin_run: int = 0
    # Berapa mesin yang keadaannya BENAR-BENAR dibaca dari lampu. Bedanya
    # dengan `mesin`: "2 dari 10 jalan" dan "2 dari 3 yang terbaca jalan"
    # adalah dua kalimat yang sangat berbeda bagi orang yang melihat layar.
    mesin_terbaca: int = 0
    rpm: int = 0
    eff: int = 0
    output: int = 0
    stops: int = 0
    area: str = ""            # nama hall
    cam: Camera = field(default_factory=lambda: Camera(id=""))
    pos: Position = field(default_factory=Position)
    machines: List[Machine] = field(default_factory=list)
    mo_groups: List[Dict[str, Any]] = field(default_factory=list)
    alert: Optional[Alert] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status_text"] = STATUS_TEXT.get(self.status, self.status)
        return d


@dataclass
class Hall:
    """Blok bangunan di denah pabrik."""
    key: str
    label: str
    x: float
    y: float
    w: float
    h: float


@dataclass
class Group:
    key: str
    label: str
    lines: List[Line] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "lines": [l.to_dict() for l in self.lines],
        }
