#!/usr/bin/env python3
"""Uji RANTAI PENUH lewat API sungguhan, persis urutan di lantai pabrik:

    lampu merah nyala   ->  mulai hitung mesin mati
    operator masuk      ->  catat jam datang
    lampu merah padam   ->  berhenti hitung, tulis ke log

    ./venv/bin/python app/uji_rantai.py

Butuh dashboard yang sedang berjalan (./run-demo.sh). Berbeda dari
app/uji_eventlog.py yang menguji aturannya saja tanpa jaringan — yang ini
melewati HTTP, penerjemahan jam, penyimpanan SQLite, sampai log teks.

Dipakai line ajl-18 karena belum punya kotak lampu, jadi tidak
berebut dengan data sungguhan.
"""
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DASHBOARD = "http://127.0.0.1:8000"
LINE = "ajl-18"
DB = Path(__file__).resolve().parent.parent / "data" / "history.db"


def kirim(status, warna, sejak, orang=0, orang_sejak=None):
    body = {"orang": orang, "orang_sejak": orang_sejak,
            "machines": [{"no": 1, "status": status, "color": warna,
                          "indikator": [], "kedip": False, "sejak": sejak}]}
    req = urllib.request.Request(
        "%s/api/lines/%s/machines" % (DASHBOARD, LINE),
        json.dumps(body).encode(), {"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=5).read()


def periksa(nama, dapat, harap):
    ok = dapat == harap
    print("  [%s] %-28s %s (harap %s)"
          % ("ok  " if ok else "GAGAL", nama, dapat, harap))
    return ok


def main():
    T = time.time()
    nyala = T - 90                 # lampu merah menyala
    datang = T - 52                # operator masuk, 38 detik kemudian
    padam = T - 35                 # lampu padam; mesin mati total 55 detik

    try:
        kirim("run", "", nyala - 10)                       # keadaan awal
        time.sleep(0.4)
        kirim("stop", "merah", nyala)                      # MERAH NYALA
        time.sleep(0.4)
        kirim("stop", "merah", nyala, 1, datang)           # OPERATOR MASUK
        time.sleep(0.4)
        kirim("run", "", padam, 1, datang)                 # MERAH PADAM
    except urllib.error.URLError as e:
        print("Dashboard tidak bisa dihubungi di %s (%s)." % (DASHBOARD, e))
        print("Jalankan ./run-demo.sh dulu.")
        return 0                   # bukan kegagalan uji, cuma tidak bisa diuji
    time.sleep(1.0)

    db = sqlite3.connect(str(DB))
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM status_episode WHERE line_id=? AND "
                     "status='stop' ORDER BY id DESC LIMIT 1", (LINE,)).fetchone()
    db.close()
    if row is None:
        print("  [GAGAL] tidak ada baris di log")
        return 1

    hasil = [
        periksa("warna tercatat", row["color"], "merah"),
        periksa("mesin mati (detik)", row["duration_sec"], 55),
        periksa("respons (detik)", row["respons_sec"], 38),
        periksa("perbaikan (detik)", row["perbaikan_sec"], 17),
        periksa("respons+perbaikan = mati",
                (row["respons_sec"] or 0) + (row["perbaikan_sec"] or 0), 55),
    ]

    teks = urllib.request.urlopen(
        "%s/api/log/%s.txt" % (DASHBOARD, LINE), timeout=5).read().decode()
    hasil.append(periksa("muncul di log teks",
                         "resp 38d  perbaikan 17d" in teks, True))

    print("\n%d/%d lulus" % (sum(hasil), len(hasil)))
    return 0 if all(hasil) else 1


if __name__ == "__main__":
    sys.exit(main())
