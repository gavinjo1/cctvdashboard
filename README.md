# CCTV Monitoring Dashboard

Dashboard monitoring CCTV + produksi untuk pabrik weaving.
Status mesin dibaca dari **lampu menara** lewat kamera, bukan dikarang.

---

## Pasang (sekali saja)

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
```

```bash
python3 -m venv venv-ai && ./venv-ai/bin/pip install -r ai/requirements.txt
```

---

## Cara jalankan

Butuh **3 terminal**. Jalankan berurutan.

**1. Dashboard**

```bash
./run-demo.sh
```

**2. Sumber kamera** (video/foto jadi kamera palsu)

```bash
./venv-ai/bin/python ai/foto_kamera.py --dir ai/foto
```

**3. Pembaca lampu**

```bash
./venv-ai/bin/python ai/worker.py --config ai/zones.json
```

Buka <http://localhost:8000>.

> Tanpa terminal 3, lampu tidak terbaca dan semua mesin abu-abu.

### Kalau kamera hitam

Browser cuma sanggup ~6 stream MJPEG sekaligus. Untuk menampilkan 18 kamera,
ganti terminal 1 dengan:

```bash
CCTV_STREAM_URL="http://127.0.0.1:1984/frame?src={id}" CCTV_STREAM_IMG_REFRESH=2 ./run-demo.sh
```

Gambar jadi patah-patah 2 detik sekali, tapi semua kamera muncul.

### Tambah pelacak operator

```bash
./venv-ai/bin/python ai/foto_kamera.py --dir ai/foto --lacak ajl-01,ajl-04
```

`--lacak` tanpa nama line = semua. **Berat**: 9 video terlacak.
Sebutkan line-nya kalau laptop mulai panas.

---

## Struktur file

| Folder | Isinya |
|---|---|
| **`ai/`** | Semua yang **melihat gambar**. Baca lampu, deteksi orang. Proses terpisah, boleh di komputer lain yang punya GPU. |
| **`app/`** | **Server dashboard**. Terima data dari `ai/`, simpan, kirim ke browser. |
| **`data/`** | **Hasil**. Database, kalibrasi. Tidak ada kode di sini. |
| `static/`, `templates/` | Tampilan web (HTML, CSS, JS). Tanpa build step. |
| `deploy/` | Berkas untuk pasang di server pabrik (systemd, nginx, go2rtc). |

### `ai/` — yang melihat gambar

| Berkas | Guna |
|---|---|
| `worker.py` | **Yang dipakai di pabrik.** Tarik RTSP kamera, baca lampu, deteksi orang, kirim ke dashboard. |
| `lamp.py` | Otak pembacaan lampu: segmen mana menyala, warnanya apa, berkedip atau tidak. |
| `foto_kamera.py` | **Untuk uji tanpa kamera.** Sajikan video/foto seolah-olah kamera. |
| `webcam_demo.py` | Uji pakai webcam laptop. |
| `zones.json` | Daftar kamera + ambang batas. **Berisi password kamera — tidak masuk git.** |
| `zones.example.json` | Contohnya. Salin jadi `zones.json` lalu isi. |
| `foto/` | Video & foto untuk uji. **Tidak masuk git** (wajah operator). |
| `foto/peta.txt` | Peta `line_id = nama berkas`. Dibaca otomatis. |
| `uji_*.py` | Tes. Lihat bagian Uji di bawah. |

### `app/` — server dashboard

| Berkas | Guna |
|---|---|
| `main.py` | Semua endpoint API + WebSocket. |
| `source.py` | Sumber data line & mesin. `LiveSource` masih kosong — **TODO pabrik**. |
| `eventlog.py` | Catat episode lampu: kapan nyala, kapan padam, operator datang jam berapa. |
| `store.py` | Simpan ke SQLite. |
| `calibration.py` | Simpan/ambil zona & kotak lampu dari UI Kalibrasi. |
| `models.py` | Bentuk data (Line, Machine, Alert). |
| `config.py` | Semua setelan lewat environment variable. |
| `auth.py`, `report.py`, `shifts.py` | Kunci API, laporan shift, jadwal shift. |

### `data/` — hasil

| Berkas | Isinya |
|---|---|
| `history.db` | Produksi, alert, laporan shift, **episode lampu**. SQLite. |
| `calibration.json` | Zona line & kotak lampu tiap mesin. **Wajib backup.** |

```bash
sqlite3 data/history.db ".backup '/backup/history.db'"
```

Jangan `cp` saat program jalan — mode WAL, salinannya bisa rusak.

---

## Kalibrasi

Tombol **Kalibrasi** di halaman detail kamera.

| Mode | Guna |
|---|---|
| **Zona Line** | Klik menambah titik. Orang dihitung kalau kakinya di dalam zona. |
| **Kotak Lampu** | Seret mengelilingi menara lampu satu mesin. |

Kotak menara harus meliputi **hanya tumpukan mika** — tanpa tutup atas dan kaki
hitam. Kalau kaki ikut, semua batas segmen bergeser dan pembacaannya salah
**tapi tetap terlihat wajar**.

Worker menariknya tiap 30 detik. Tidak perlu restart.

---

## Arti lampu

Diisi di `zones.json`, urut **atas → bawah**:

```json
"indikator": ["lusi_putus", "pakan_putus", "putih", "kuning"],
"warna_menara": ["merah", "hijau", "putih", "kuning"],
"saat_gelap": "run"
```

Di pabrik ini (Toyota JAT810):

| | |
|---|---|
| semua padam | mesin jalan normal |
| hijau | pakan putus |
| merah | lusi putus |
| putih, kuning | **belum dipastikan** |

`warna_menara` = warna fisik tiap posisi. Dipakai **memeriksa kotak**, bukan
menentukan arti. Kalau segmen ke-2 menyala tapi pikselnya merah padahal
seharusnya hijau, kotaknya meleset — worker menulis `WARNA TIDAK COCOK`.

`saat_gelap` = status saat tidak ada segmen menyala. **Salah pilih membuat
status semua mesin terbalik.** Pastikan dengan melihat satu mesin yang sedang
berproduksi.

Lampu dibaca **25 fps** karena sinyalnya berkedip. Pada 3 fps, kedip 3 Hz tidak
terdeteksi sama sekali.

---

## Uji

```bash
./venv-ai/bin/python ai/uji_worker.py        # aturan pengiriman alert
./venv-ai/bin/python ai/uji_warna_lampu.py   # pembacaan warna lampu
```

Keduanya tanpa dashboard, kamera, atau model.

Uji pembacaan lampu di video sendiri:

```bash
./venv-ai/bin/python ai/uji_lampu_video.py rekaman.mp4 --kisi 5     # cari kotak
./venv-ai/bin/python ai/uji_lampu_video.py rekaman.mp4 --tower 46,10,6,46
```

**Periksa `overlay.png` dulu** sebelum percaya angkanya. Kotak yang meleset
memberi angka yang salah tapi tetap masuk akal.

---



```bash
sudo cp deploy/cctv-dashboard.service deploy/cctv-ai.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now cctv-dashboard cctv-ai
```

Isi dulu di berkas service: `CCTV_API_KEY`, `CCTV_SOURCE=live`, `CCTV_STREAM_URL`.

Browser tidak bisa membaca RTSP langsung — pakai **go2rtc**, nama stream harus
sama dengan `line_id`:

```bash
go2rtc -config deploy/go2rtc.yaml
```

Grid 18 kamera **wajib** pakai sub-stream (`CCTV_STREAM_URL_GRID`).

Cek kesiapan:

```bash
curl -s http://SERVER:8000/healthz | python3 -m json.tool
```


```bash
grep -rn "TODO(pabrik)" app/ ai/
```
