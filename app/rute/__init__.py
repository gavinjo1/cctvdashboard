"""Endpoint HTTP, dikelompokkan menurut apa yang diurusnya.

Satu berkas = satu urusan. Dulu semuanya di app/main.py: 26 endpoint dalam
satu berkas 627 baris, jadi mencari "di mana MO disimpan" berarti menggulir
melewati alert, kalibrasi, dan laporan shift.

    line.py       keadaan line & mesin (termasuk kiriman dari AI worker)
    alert.py      alert masuk, penutupan alert, daftar operator
    kalibrasi.py  zona line & kotak lampu
    mo.py         penugasan MO per mesin
    laporan.py    riwayat, laporan shift, ringkasan periode
    log.py        catatan episode lampu (unduh .txt)

Semuanya mengambil objek bersama dari app/state.py, bukan dari main.py —
supaya tidak ada impor melingkar.
"""
