"""Pencatat episode perubahan warna lampu, untuk pengumpulan data uji.

Yang dicatat adalah EPISODE, bukan cuplikan per detik:

    10:00:02 - 10:04:31  mesin 01  nyala   (hijau)   4m 29d

Alasannya sederhana. Kamera membaca warna beberapa kali per detik; mencatat
tiap pembacaan akan menghasilkan ratusan ribu baris per hari yang tidak bisa
dibaca manusia. Yang berguna untuk pengujian adalah kapan warna BERUBAH.

Episode ditutup ketika warna berganti, dan disimpan ke basis data. Berkas
teks dibuat saat diunduh, bukan ditumpuk di disk — supaya tidak ada dua
sumber kebenaran yang bisa berbeda isi.
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("cctv")

#: status mesin -> kata yang dipakai di berkas log
KATA = {"run": "nyala", "idle": "setting", "stop": "mati", "off": "padam"}
WARNA = {"green": "hijau", "yellow": "kuning", "red": "merah",
         "off": "padam", "": "-"}


def durasi(detik: float) -> str:
    d = int(detik)
    if d < 60:
        return "%dd" % d
    if d < 3600:
        return "%dm %02dd" % (d // 60, d % 60)
    return "%dj %02dm" % (d // 3600, (d % 3600) // 60)


class Episode:
    __slots__ = ("line_id", "machine_no", "status", "color", "started", "ended")

    def __init__(self, line_id, machine_no, status, color, started):
        self.line_id = line_id
        self.machine_no = machine_no
        self.status = status
        self.color = color
        self.started = started
        self.ended: Optional[datetime] = None

    @property
    def seconds(self) -> float:
        end = self.ended or datetime.now()
        return max(0.0, (end - self.started).total_seconds())

    def row(self) -> dict:
        return {
            "line_id": self.line_id,
            "machine_no": self.machine_no,
            "status": self.status,
            "color": self.color,
            "started_at": self.started.isoformat(timespec="seconds"),
            "ended_at": self.ended.isoformat(timespec="seconds")
            if self.ended else None,
            "duration_sec": round(self.seconds),
        }

    def teks(self) -> str:
        akhir = self.ended.strftime("%H:%M:%S") if self.ended else "  berjalan"
        return "%s - %s  mesin %s  %-8s (%-7s) %10s" % (
            self.started.strftime("%H:%M:%S"), akhir,
            str(self.machine_no).zfill(2),
            KATA.get(self.status, self.status),
            WARNA.get(self.color, self.color or "-"),
            durasi(self.seconds))


class EpisodeTracker:
    """Melacak episode terbuka tiap mesin dan menutupnya saat warna berubah."""

    def __init__(self, on_close=None, min_seconds: int = 0):
        # episode yang sedang berjalan, kunci (line_id, machine_no)
        self.open: Dict[Tuple[str, int], Episode] = {}
        self.on_close = on_close          # dipanggil saat satu episode ditutup
        self.min_seconds = min_seconds    # episode lebih pendek dari ini diabaikan

    def observe(self, line_id: str, machine_no: int, status: str,
                color: str = "", now: Optional[datetime] = None) -> Optional[Episode]:
        """Catat pembacaan. Return episode yang baru ditutup, bila ada."""
        now = now or datetime.now()
        key = (line_id, int(machine_no))
        cur = self.open.get(key)

        # warna & status masih sama -> tidak ada yang perlu dicatat
        if cur is not None and cur.status == status and cur.color == color:
            return None

        closed = None
        if cur is not None:
            cur.ended = now
            if cur.seconds >= self.min_seconds:
                closed = cur
                if self.on_close:
                    self.on_close(cur)

        self.open[key] = Episode(line_id, int(machine_no), status, color, now)
        return closed

    def observe_many(self, line_id: str, machines: List[dict]) -> int:
        now = datetime.now()
        n = 0
        for m in machines:
            no = m.get("no")
            if no is None:
                continue
            if self.observe(line_id, no, m.get("status", "off"),
                            m.get("color", ""), now) is not None:
                n += 1
        return n

    def running(self, line_id: str = None) -> List[Episode]:
        """Episode yang masih berjalan (belum ditutup)."""
        out = [e for k, e in self.open.items()
               if line_id is None or k[0] == line_id]
        return sorted(out, key=lambda e: e.machine_no)

    def reset(self, line_id: str = None) -> None:
        if line_id is None:
            self.open.clear()
        else:
            for k in [k for k in self.open if k[0] == line_id]:
                del self.open[k]


# ---------------------------------------------------------------- berkas teks
def buat_teks(line_name: str, line_id: str, rows: List[dict],
              berjalan: List[Episode] = None) -> str:
    """Susun isi berkas .txt dari episode yang tersimpan."""
    baris = []
    B = baris.append

    B("=" * 72)
    B("LOG DETEKSI WARNA LAMPU TOWER")
    B("Line   : %s (%s)" % (line_name, line_id))
    B("Dibuat : %s" % datetime.now().strftime("%d/%m/%Y %H:%M:%S"))
    B("Jumlah : %d episode" % len(rows))
    B("=" * 72)
    B("")

    if not rows:
        B("Belum ada data. Pastikan AI worker berjalan dan mengirim status")
        B("mesin ke dasbor, lalu tunggu sampai ada warna lampu yang berubah.")
        return "\n".join(baris) + "\n"

    # ---- bagian 1: berurutan waktu ----
    B("-" * 72)
    B("URUT WAKTU")
    B("-" * 72)
    tanggal_terakhir = None
    for r in rows:
        mulai = datetime.fromisoformat(r["started_at"])
        tgl = mulai.strftime("%d/%m/%Y")
        if tgl != tanggal_terakhir:
            tanggal_terakhir = tgl
            B("")
            B("[ %s ]" % tgl)
        akhir = datetime.fromisoformat(r["ended_at"]).strftime("%H:%M:%S") \
            if r["ended_at"] else "  berjalan"
        B("  %s - %s  mesin %s  %-8s (%-7s) %10s" % (
            mulai.strftime("%H:%M:%S"), akhir,
            str(r["machine_no"]).zfill(2),
            KATA.get(r["status"], r["status"]),
            WARNA.get(r["color"], r["color"] or "-"),
            durasi(r["duration_sec"])))

    # ---- bagian 2: dikelompokkan per mesin ----
    B("")
    B("-" * 72)
    B("PER MESIN")
    B("-" * 72)
    per_mesin: Dict[int, List[dict]] = {}
    for r in rows:
        per_mesin.setdefault(r["machine_no"], []).append(r)

    for no in sorted(per_mesin):
        eps = per_mesin[no]
        total = {}
        for r in eps:
            total[r["status"]] = total.get(r["status"], 0) + r["duration_sec"]
        ringkas = "  ".join("%s %s" % (KATA.get(k, k), durasi(v))
                            for k, v in sorted(total.items()))
        B("")
        B("MESIN %s   %d episode   %s" % (str(no).zfill(2), len(eps), ringkas))
        for r in eps:
            mulai = datetime.fromisoformat(r["started_at"])
            akhir = datetime.fromisoformat(r["ended_at"]).strftime("%H:%M:%S") \
                if r["ended_at"] else "berjalan"
            B("   %-8s jam %s - %s   (%s)" % (
                KATA.get(r["status"], r["status"]),
                mulai.strftime("%H:%M:%S"), akhir, durasi(r["duration_sec"])))

    # ---- bagian 3: yang masih berjalan ----
    if berjalan:
        B("")
        B("-" * 72)
        B("MASIH BERJALAN saat berkas ini dibuat")
        B("-" * 72)
        for e in berjalan:
            B("  mesin %s  %-8s (%-7s) sejak %s  sudah %s" % (
                str(e.machine_no).zfill(2),
                KATA.get(e.status, e.status),
                WARNA.get(e.color, e.color or "-"),
                e.started.strftime("%H:%M:%S"), durasi(e.seconds)))

    B("")
    return "\n".join(baris) + "\n"
