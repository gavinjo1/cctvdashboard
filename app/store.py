"""Penyimpanan histori & jejak audit (SQLite).

Tanpa modul ini semua data hanya ada di memori: satu restart dashboard
menghapus output, jumlah stop, dan seluruh riwayat alert.

Tiga hal yang disimpan:
  1. production  — snapshot produksi berkala, untuk laporan & tren
  2. alerts      — setiap deteksi AI, termasuk siapa yang menutupnya
  3. state       — penghitung berjalan, supaya restart tidak menolkan angka

SQLite dipilih karena tidak butuh server terpisah dan cukup untuk
beban ini (18 line x 1 baris tiap 5 menit ≈ 1,9 juta baris/tahun).
TODO(pabrik): kalau nanti butuh dibaca sistem lain (BI, ERP), pindahkan
ke PostgreSQL — cukup ganti isi kelas ini, pemanggilnya tidak berubah.
"""
import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("cctv")

SCHEMA = """
CREATE TABLE IF NOT EXISTS production (
    ts          TEXT    NOT NULL,
    period_key  TEXT    NOT NULL,
    shift       TEXT    NOT NULL,
    line_id     TEXT    NOT NULL,
    status      TEXT    NOT NULL,
    mesin_run   INTEGER NOT NULL,
    mesin_total INTEGER NOT NULL,
    rpm         INTEGER NOT NULL,
    eff         INTEGER NOT NULL,
    output      INTEGER NOT NULL,
    stops       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_prod_ts   ON production(ts);
CREATE INDEX IF NOT EXISTS ix_prod_line ON production(line_id, ts);

CREATE TABLE IF NOT EXISTS alerts (
    id          TEXT PRIMARY KEY,
    line_id     TEXT NOT NULL,
    label       TEXT NOT NULL,
    activity    TEXT,
    object_type TEXT,
    confidence  INTEGER,
    duration    INTEGER,
    severity    TEXT,
    detected_at TEXT,
    created_at  TEXT NOT NULL,
    resolved_at TEXT,
    resolved_by TEXT,
    action      TEXT
);
CREATE INDEX IF NOT EXISTS ix_alert_line ON alerts(line_id, created_at);
CREATE INDEX IF NOT EXISTS ix_alert_open ON alerts(resolved_at);

CREATE TABLE IF NOT EXISTS shift_report (
    period_key  TEXT    NOT NULL,
    shift       TEXT    NOT NULL,
    started_at  TEXT    NOT NULL,
    ended_at    TEXT    NOT NULL,
    line_id     TEXT    NOT NULL,
    machine_no  INTEGER,            -- NULL = baris ringkasan line
    name        TEXT,
    operator    TEXT,
    eff_avg     INTEGER NOT NULL,   -- rata-rata dibobot waktu, seluruh periode
    eff_run     INTEGER NOT NULL,   -- rata-rata hanya saat beroperasi
    rpm_avg     INTEGER NOT NULL,
    availability INTEGER NOT NULL,  -- persen waktu beroperasi
    sec_run     INTEGER NOT NULL,
    sec_idle    INTEGER NOT NULL,
    sec_stop    INTEGER NOT NULL,
    sec_off     INTEGER NOT NULL,
    sec_total   INTEGER NOT NULL,
    output      INTEGER NOT NULL,
    stops       INTEGER NOT NULL,
    downtime    INTEGER NOT NULL,
    mesin_avg   REAL,
    PRIMARY KEY (period_key, line_id, machine_no)
);
CREATE INDEX IF NOT EXISTS ix_rep_period ON shift_report(period_key);

CREATE TABLE IF NOT EXISTS status_episode (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id      TEXT    NOT NULL,
    machine_no   INTEGER NOT NULL,
    status       TEXT    NOT NULL,   -- run | idle | stop | off
    color        TEXT,               -- green | yellow | red | off
    started_at   TEXT    NOT NULL,
    ended_at     TEXT,
    duration_sec INTEGER NOT NULL,
    operator_at  TEXT,               -- kapan operator pertama terlihat
    respons_sec  INTEGER,            -- lampu nyala -> operator datang
    perbaikan_sec INTEGER            -- operator datang -> lampu padam
);
CREATE INDEX IF NOT EXISTS ix_ep_line ON status_episode(line_id, started_at);

CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    ts    TEXT NOT NULL
);
"""


class HistoryStore:
    def __init__(self, path: str = "data/history.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL: penulisan tidak memblokir pembacaan, lebih tahan mati listrik
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        log.info("Database histori siap: %s", self.path)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---------- produksi ----------
    def save_production(self, lines, period_key: str, shift: str) -> int:
        ts = datetime.now().isoformat(timespec="seconds")
        rows = [(ts, period_key, shift, l.id, l.status, l.mesin_run, l.mesin,
                 l.rpm, l.eff, l.output, l.stops) for l in lines]
        with self._lock:
            self._conn.executemany(
                "INSERT INTO production (ts,period_key,shift,line_id,status,"
                "mesin_run,mesin_total,rpm,eff,output,stops) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            self._conn.commit()
        return len(rows)

    def production_history(self, line_id: str = None, hours: int = 24) -> List[dict]:
        since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
        q = "SELECT * FROM production WHERE ts >= ?"
        args = [since]
        if line_id:
            q += " AND line_id = ?"
            args.append(line_id)
        q += " ORDER BY ts"
        with self._lock:
            return [dict(r) for r in self._conn.execute(q, args)]

    def shift_summary(self, period_key: str) -> List[dict]:
        """Rekap satu periode: nilai terakhir tiap line."""
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT line_id, MAX(ts) AS ts, output, stops, eff, shift "
                "FROM production WHERE period_key = ? GROUP BY line_id "
                "ORDER BY line_id", (period_key,))]

    # ---------- episode warna lampu (pengumpulan data uji) ----------
    def save_episode(self, ep: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO status_episode (line_id,machine_no,status,color,"
                "started_at,ended_at,duration_sec,operator_at,respons_sec,"
                "perbaikan_sec) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ep["line_id"], ep["machine_no"], ep["status"], ep["color"],
                 ep["started_at"], ep["ended_at"], ep["duration_sec"],
                 ep.get("operator_at"), ep.get("respons_sec"),
                 ep.get("perbaikan_sec")))
            self._conn.commit()

    def episodes(self, line_id: str, hours: int = 0,
                 limit: int = 20000) -> List[dict]:
        q = "SELECT * FROM status_episode WHERE line_id = ?"
        args: list = [line_id]
        if hours:
            since = (datetime.now() - timedelta(hours=hours)) \
                .isoformat(timespec="seconds")
            q += " AND started_at >= ?"
            args.append(since)
        q += " ORDER BY started_at, machine_no LIMIT ?"
        args.append(limit)
        with self._lock:
            return [dict(r) for r in self._conn.execute(q, args)]

    def episode_lines(self) -> List[dict]:
        """Line yang sudah punya catatan episode, beserta jumlahnya."""
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT line_id, COUNT(*) n, MIN(started_at) awal, "
                "MAX(started_at) akhir FROM status_episode "
                "GROUP BY line_id ORDER BY line_id")]

    def clear_episodes(self, line_id: str = None) -> int:
        with self._lock:
            if line_id:
                n = self._conn.execute(
                    "DELETE FROM status_episode WHERE line_id = ?",
                    (line_id,)).rowcount
            else:
                n = self._conn.execute("DELETE FROM status_episode").rowcount
            self._conn.commit()
        return n

    # ---------- laporan shift ----------
    COLS = ("period_key", "shift", "started_at", "ended_at", "line_id",
            "machine_no", "name", "operator", "eff_avg", "eff_run", "rpm_avg",
            "availability", "sec_run", "sec_idle", "sec_stop", "sec_off",
            "sec_total", "output", "stops", "downtime", "mesin_avg")

    def save_shift_report(self, rows: List[dict]) -> int:
        """Simpan ringkasan periode. Ditulis sekali saat periode berakhir."""
        if not rows:
            return 0
        placeholders = ",".join("?" * len(self.COLS))
        data = [tuple(r.get(c) for c in self.COLS) for r in rows]
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO shift_report (%s) VALUES (%s)"
                % (",".join(self.COLS), placeholders), data)
            self._conn.commit()
        log.info("Laporan periode %s disimpan: %d baris",
                 rows[0]["period_key"], len(rows))
        return len(rows)

    def shift_report_rows(self, period_key: str) -> List[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT * FROM shift_report WHERE period_key = ? "
                "ORDER BY line_id, machine_no", (period_key,))]

    def report_periods(self, limit: int = 60) -> List[dict]:
        """Daftar periode yang laporannya sudah tersimpan, terbaru dulu."""
        with self._lock:
            return [dict(r) for r in self._conn.execute(
                "SELECT period_key, shift, MIN(started_at) started_at, "
                "MAX(ended_at) ended_at, COUNT(*) baris "
                "FROM shift_report GROUP BY period_key, shift "
                "ORDER BY started_at DESC LIMIT ?", (limit,))]

    # ---------- alert ----------
    def log_alert(self, alert) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO alerts (id,line_id,label,activity,"
                "object_type,confidence,duration,severity,detected_at,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (alert.id, alert.line_id, alert.label, alert.activity,
                 alert.object_type, alert.confidence, alert.duration,
                 alert.severity, alert.detected_at,
                 datetime.now().isoformat(timespec="seconds")))
            self._conn.commit()

    def resolve_alert(self, alert_id: str, action: str, by: str) -> None:
        """Catat SIAPA yang menutup alert — inti dari jejak audit."""
        with self._lock:
            self._conn.execute(
                "UPDATE alerts SET resolved_at=?, resolved_by=?, action=? "
                "WHERE id=? AND resolved_at IS NULL",
                (datetime.now().isoformat(timespec="seconds"), by, action, alert_id))
            self._conn.commit()

    def alert_history(self, line_id: str = None, days: int = 7,
                      limit: int = 500) -> List[dict]:
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        q = "SELECT * FROM alerts WHERE created_at >= ?"
        args = [since]
        if line_id:
            q += " AND line_id = ?"
            args.append(line_id)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            return [dict(r) for r in self._conn.execute(q, args)]

    def alert_stats_period(self, period_key: str, started: str, ended: str) -> dict:
        """Rekap alert dalam rentang satu periode shift."""
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) c FROM alerts WHERE created_at BETWEEN ? AND ?",
                (started, ended)).fetchone()["c"]
            by_label = [dict(r) for r in self._conn.execute(
                "SELECT label, COUNT(*) c FROM alerts "
                "WHERE created_at BETWEEN ? AND ? GROUP BY label ORDER BY c DESC",
                (started, ended))]
            avg = self._conn.execute(
                "SELECT AVG(strftime('%s',resolved_at) - strftime('%s',created_at)) a "
                "FROM alerts WHERE created_at BETWEEN ? AND ? "
                "AND resolved_at IS NOT NULL", (started, ended)).fetchone()["a"]
            open_now = self._conn.execute(
                "SELECT COUNT(*) c FROM alerts WHERE created_at BETWEEN ? AND ? "
                "AND resolved_at IS NULL", (started, ended)).fetchone()["c"]
        return {
            "total": total,
            "belum_ditangani": open_now,
            "avg_response_sec": int(avg) if avg is not None else None,
            "by_label": by_label,
        }

    def alert_stats(self, days: int = 7) -> dict:
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) c FROM alerts WHERE created_at >= ?",
                (since,)).fetchone()["c"]
            by_label = [dict(r) for r in self._conn.execute(
                "SELECT label, COUNT(*) c FROM alerts WHERE created_at >= ? "
                "GROUP BY label ORDER BY c DESC", (since,))]
            false_alarm = self._conn.execute(
                "SELECT COUNT(*) c FROM alerts WHERE created_at >= ? "
                "AND action = 'false'", (since,)).fetchone()["c"]
            # rata-rata waktu tanggap (detik)
            avg = self._conn.execute(
                "SELECT AVG(strftime('%s',resolved_at) - strftime('%s',created_at)) a "
                "FROM alerts WHERE created_at >= ? AND resolved_at IS NOT NULL",
                (since,)).fetchone()["a"]
        return {
            "days": days,
            "total": total,
            "false_alarm": false_alarm,
            "false_alarm_pct": round(false_alarm / total * 100) if total else 0,
            # avg bisa 0 (ditutup < 1 detik) — 0 berbeda dengan "belum ada data"
            "avg_response_sec": int(avg) if avg is not None else None,
            "by_label": by_label,
        }

    # ---------- state (tahan restart) ----------
    def save_state(self, key: str, value: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO state (key,value,ts) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts",
                (key, json.dumps(value),
                 datetime.now().isoformat(timespec="seconds")))
            self._conn.commit()

    def load_state(self, key: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return None

    # ---------- perawatan ----------
    def purge(self, retention_days: int) -> int:
        """Buang data lama supaya disk tidak penuh. 0 = simpan selamanya."""
        if not retention_days:
            return 0
        cutoff = (datetime.now() - timedelta(days=retention_days)) \
            .isoformat(timespec="seconds")
        with self._lock:
            n = self._conn.execute(
                "DELETE FROM production WHERE ts < ?", (cutoff,)).rowcount
            n += self._conn.execute(
                "DELETE FROM alerts WHERE created_at < ?", (cutoff,)).rowcount
            # laporan shift jauh lebih ringkas — simpan dua kali lebih lama
            rep_cutoff = (datetime.now() - timedelta(days=retention_days * 2)) \
                .isoformat(timespec="seconds")
            n += self._conn.execute(
                "DELETE FROM shift_report WHERE ended_at < ?",
                (rep_cutoff,)).rowcount
            self._conn.commit()
        if n:
            log.info("Histori lama dibuang: %d baris (> %d hari)", n, retention_days)
        return n
