# CCTV Monitoring Dashboard

Dashboard monitoring CCTV + produksi untuk pabrik weaving.
**1 kamera = 1 line = 10 mesin = 1 operator.**

Backend Python (FastAPI), frontend tanpa build step. AI worker proses terpisah,
boleh di mesin lain yang punya GPU.

> Rincian go-live ada di [PRODUCTION.md](PRODUCTION.md).

---

## Pasang

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
```

AI worker (opsional, boleh di mesin lain):

```bash
python3 -m venv venv-ai && ./venv-ai/bin/pip install -r ai/requirements.txt
```

## Jalankan

```bash
./venv/bin/python run.py
```

Buka <http://localhost:8000>. Semua setelan lewat environment variable.

---

## Menguji tanpa CCTV

Tiga cara, pilih salah satu. Semuanya butuh **dua terminal**:
sumber gambar di satu, dashboard di satu lagi.

### A. Foto diam — paling mudah untuk menyetel kotak lampu

Simpan satu foto per line, namanya = `line_id`:

```
ai/foto/ajl-01.png
ai/foto/ajl-02.png
```

```bash
./venv-ai/bin/python ai/foto_kamera.py --dir ai/foto
```

```bash
./run-demo.sh
```

Foto dibaca ulang tiap permintaan — timpa berkasnya, muat ulang halaman,
tanpa restart. Foto non-16:9 otomatis diberi bilah agar kotak kalibrasi
tidak meleset.

### B. Berkas video

```bash
./venv-ai/bin/python ai/webcam_demo.py --source /tmp/uji.mp4 --loop --line-id ajl-01
```

```bash
./run-demo.sh
```

> **Salin dulu videonya.** Jangan arahkan alat apa pun ke satu-satunya salinan.

### C. Webcam laptop

```bash
./venv-ai/bin/python ai/webcam_demo.py --device 0
```

```bash
./run-demo.sh
```

---

## Produksi

### Dashboard

```bash
sudo cp deploy/cctv-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now cctv-dashboard
```

Isi dulu di berkas service itu: `CCTV_API_KEY`, `CCTV_SOURCE=live`,
`CCTV_STREAM_URL`. Cek kesiapan:

```bash
curl -s http://SERVER:8000/healthz | python3 -m json.tool
```

`"production_ready": true` berarti pemeriksaan otomatis lolos.

### Stream kamera (go2rtc)

Browser tidak bisa membaca RTSP langsung. Isi `deploy/go2rtc.yaml` —
**nama stream harus sama dengan `line_id`**.

```bash
go2rtc -config deploy/go2rtc.yaml
```

```bash
CCTV_STREAM_URL="http://IP:1984/api/stream.mp4?src={id}"
CCTV_STREAM_URL_GRID="http://IP:1984/api/stream.mp4?src={id}-sub"
```

Grid 18 kamera **wajib** pakai sub-stream, atau PC operator tersendat.

### AI worker

```bash
cp ai/zones.example.json ai/zones.json    # isi RTSP + api_key
chmod 600 ai/zones.json                   # berisi password kamera
./venv-ai/bin/python ai/worker.py --config ai/zones.json
```

---

## Kalibrasi

Tombol **Kalibrasi** di halaman detail kamera.

| Mode | Guna |
|---|---|
| **Zona Line** | Klik menambah titik. Orang dihitung kalau titik kakinya di dalam zona |
| **Kotak Lampu** | Seret di sekeliling menara lampu. "Bagi Rata" membuat 10 kotak sekaligus |

Kotak menara harus meliputi **hanya tumpukan mika** — tanpa tutup atas dan kaki
hitam. Kalau kaki ikut terkotak, semua batas segmen bergeser dan pembacaannya
salah **tapi tetap terlihat wajar**.

Tersimpan di `data/calibration.json`, ditarik AI worker lewat API tiap 30 detik.

### Menguji pembacaan lampu di luar dashboard

```bash
./venv-ai/bin/python ai/uji_lampu_video.py rekaman.mp4 --kisi 5     # cari kotak
./venv-ai/bin/python ai/uji_lampu_video.py rekaman.mp4 \
    --tower 46,10,6,46 --indikator putih,merah,kuning,hijau
```

Kolom `margin` = jarak ke ambang. Menyala harus jelas **positif**, padam jelas
**negatif**. Berdempet di nol berarti kotaknya meleset — perbaiki kotak dulu,
bukan ambangnya.

---

## Menara lampu

Arti datang dari **posisi segmen**, bukan warna. Diisi di `ai/zones.json`:

```json
"indikator": ["loose_weft", "mesin_stop", "putus_pakan", "jalan"],
"saat_gelap": "run"
```

`indikator` urut **atas → bawah**.

`saat_gelap` = status saat tidak ada segmen menyala:
`"run"` (menara padam saat mesin jalan) atau `"off"` (hijau menyala terus).
**Salah pilih membuat status semua mesin terbalik.** Pastikan dengan melihat
satu mesin yang sedang berproduksi.

Baca lampu di **25–30 fps** — sinyalnya kedip, dan pada 3 fps kedip 3 Hz tidak
terdeteksi sama sekali. Pembacaan lampu murah (tanpa GPU); deteksi orang boleh
jauh lebih lambat.

---

## Data

Semua di `data/` pada server dashboard:

| | |
|---|---|
| `history.db` | produksi, alert, laporan shift, episode lampu |
| `calibration.json` | zona & kotak lampu — **wajib backup** |
| `operators.json` | nama yang boleh menutup alert |

```bash
sqlite3 data/history.db ".backup '/backup/history.db'"
```

Jangan `cp` saat berjalan — mode WAL, salinannya bisa sobek.

---

## API

| | |
|---|---|
| `GET /api/lines` | snapshot semua line |
| `GET /healthz` | status + daftar yang belum siap produksi |
| `WS /ws` | update realtime |
| `POST /api/alerts` | kirim deteksi (butuh `X-API-Key`) |
| `POST /api/lines/{id}/machines` | status mesin dari lampu (butuh `X-API-Key`) |
| `GET /api/report/shift` | laporan shift |
| `GET /api/docs` | dokumentasi lengkap |

```bash
curl -X POST http://SERVER:8000/api/alerts \
  -H "Content-Type: application/json" -H "X-API-Key: TOKEN" \
  -d '{"line_id":"ajl-01","label":"Mesin stop tanpa penanganan","duration":312}'
```

---

## Catatan

- **Tetap 1 worker uvicorn.** State di memori + WebSocket butuh koneksi sticky
- nginx wajib meneruskan header `Upgrade`/`Connection` — `deploy/nginx-cctv.conf` sudah benar
- Server, AI worker, dan NVR harus **satu zona waktu + NTP**. Kalau tidak, waktu
  alert tidak cocok dengan rekaman CCTV dan alert tidak bisa diverifikasi
- Sebelum produksi: `grep -rn "TODO(pabrik)" app/ ai/`
