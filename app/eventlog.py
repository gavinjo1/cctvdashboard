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
    __slots__ = ("line_id", "machine_no", "status", "color", "started",
                 "ended", "operator_at")

    def __init__(self, line_id, machine_no, status, color, started):
        self.line_id = line_id
        self.machine_no = machine_no
        self.status = status
        self.color = color
        self.started = started
        self.ended: Optional[datetime] = None
        # Kapan operator PERTAMA terlihat selama episode ini berlangsung.
        # MESIN MANA YANG DIPERBAIKI DITUNJUK OLEH LAMPUNYA, bukan oleh
        # posisi orangnya: lampu yang padam itulah mesin yang beres. Cara ini
        # tidak butuh zona lantai per mesin, dan tidak butuh mengenali
        # "sedang menyambung benang" — mesinnya sendiri yang memberi tahu
        # kapan pekerjaan selesai.
        self.operator_at: Optional[datetime] = None

    @property
    def seconds(self) -> float:
        end = self.ended or datetime.now()
        return max(0.0, (end - self.started).total_seconds())

    @property
    def respons_sec(self) -> Optional[int]:
        """Lampu menyala -> operator datang."""
        if self.operator_at is None:
            return None
        return max(0, round((self.operator_at - self.started).total_seconds()))

    @property
    def perbaikan_sec(self) -> Optional[int]:
        """Operator datang -> lampu padam. Kosong kalau tidak ada operator."""
        if self.operator_at is None or self.ended is None:
            return None
        return max(0, round((self.ended - self.operator_at).total_seconds()))

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
            "operator_at": self.operator_at.isoformat(timespec="seconds")
            if self.operator_at else None,
            "respons_sec": self.respons_sec,
            "perbaikan_sec": self.perbaikan_sec,
        }

    def teks(self) -> str:
        akhir = self.ended.strftime("%H:%M:%S") if self.ended else "  berjalan"
        # Pembagian waktu hanya ditulis kalau operator memang terlihat.
        # Kolom kosong = tidak ada orang selama lampu menyala; itu keterangan
        # yang berguna, bukan data yang hilang.
        op = ""
        if self.respons_sec is not None:
            op = "  resp %s  perbaikan %s" % (
                durasi(self.respons_sec).strip(),
                durasi(self.perbaikan_sec or 0).strip())
        return "%s - %s  mesin %s  %-8s (%-7s) %10s%s" % (
            self.started.strftime("%H:%M:%S"), akhir,
            str(self.machine_no).zfill(2),
            KATA.get(self.status, self.status),
            WARNA.get(self.color, self.color or "-"),
            durasi(self.seconds), op)


#: Jam transisi yang lebih tua dari ini diabaikan — jam worker yang meleset
#: jauh (zona waktu salah, NTP belum sinkron) lebih berbahaya daripada jeda
#: kirim yang mau diperbaiki.
BATAS_MUNDUR_DETIK = 3600


def _jam_transisi(sejak, tiba: datetime) -> datetime:
    """Ubah 'sejak' (epoch dari worker) jadi datetime yang bisa dipercaya."""
    if sejak is None:
        return tiba
    try:
        t = datetime.fromtimestamp(float(sejak))
    except (TypeError, ValueError, OSError, OverflowError):
        return tiba
    selisih = (tiba - t).total_seconds()
    if selisih < -5 or selisih > BATAS_MUNDUR_DETIK:
        return tiba          # jam worker tidak masuk akal, jangan dipercaya
    return t


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
            # Operator yang baru muncul SETELAH lampu padam bukan yang
            # memperbaikinya. Tanpa penjagaan ini, respons bisa tercatat
            # lebih lama daripada episodenya sendiri.
            if cur.operator_at is not None and cur.operator_at > cur.ended:
                cur.operator_at = None
            if cur.seconds >= self.min_seconds:
                closed = cur
                if self.on_close:
                    self.on_close(cur)

        self.open[key] = Episode(line_id, int(machine_no), status, color, now)
        return closed

    def catat_operator(self, line_id: str, saat: datetime) -> None:
        """Tandai operator terlihat: dicatat ke tiap episode BERMASALAH
        yang sedang berjalan di line ini.

        Kalau dua mesin bermasalah bersamaan, keduanya ikut tertandai —
        kamera tidak bisa memastikan operator menangani yang mana. Yang
        memisahkan nanti adalah lampu mana yang padam duluan.
        """
        for ep in self.open.values():
            if ep.line_id != line_id or ep.status == "run":
                continue
            if ep.operator_at is None and saat >= ep.started:
                ep.operator_at = saat

    def observe_many(self, line_id: str, machines: List[dict],
                     orang: int = 0, orang_sejak=None) -> int:
        """Catat pembacaan sekelompok mesin.

        JAM TRANSISI DIPAKAI KALAU DIKIRIM. Worker membaca lampu 25x per
        detik dan tahu persis detik ke berapa lampu berubah, tetapi paketnya
        berangkat tiap 10 detik. Kalau di sini dipakai datetime.now(), jam
        itu hilang dan tiap episode meleset sampai sepanjang jeda kirim —
        terlihat jelas di data lama: empat mesin "berhenti" pada detik yang
        sama persis, berulang, selalu di kelipatan 10 detik.
        """
        tiba = datetime.now()
        if orang > 0:
            self.catat_operator(line_id, _jam_transisi(orang_sejak, tiba))
        n = 0
        for m in machines:
            no = m.get("no")
            if no is None:
                continue
            saat = _jam_transisi(m.get("sejak"), tiba)
            if self.observe(line_id, no, m.get("status", "off"),
                            m.get("color", ""), saat) is not None:
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
