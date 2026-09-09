# CCTV Monitoring Dashboard

Dashboard monitoring CCTV + produksi untuk pabrik weaving.

**1 kamera = 1 line = 10 mesin = 1 operator.**
Tiap mesin bisa mengerjakan MO yang berbeda.

Backend Python (FastAPI), frontend HTML/CSS/JS tanpa build step — tidak butuh
Node.js di server pabrik. AI berjalan sebagai proses terpisah dan boleh
dipasang di mesin lain.

---

## Isinya apa

Tiga view, dipilih dari rail ikon di kiri:

| View | Isi |
|---|---|
| **Camera** | Grid kartu kamera per line, kode MO di kaki kartu. Klik kartu → halaman detail: feed besar, rincian Order MO per mesin, panel alert, dan tombol Kalibrasi |
| **Map** | Denah pabrik: hall, posisi line, titik kamera, 10 kotak kecil = 10 mesin (warna = status). Line yang alert berkedip merah. Klik line → ke detail kamera |
| **Analysis** | Daftar line tanpa feed. Klik satu line → tabel 10 mesin (Order MO, status, RPM, efisiensi, output, stop, downtime), dikelompokkan per MO |

Panel atas: total line, line running, idle, stop, mesin jalan, efisiensi
rata-rata, output hari ini. Lonceng kanan atas menampilkan jumlah alert aktif;
klik untuk memfilter kamera yang sedang alert.

### Order MO per mesin

Satu line berisi 10 mesin dan tiap mesin bisa mengerjakan MO berbeda. Di
halaman detail kamera, mesin dikelompokkan otomatis per MO:

```
MO-7360   6 mesin    [01] [02] [03] [04] [06] [08]    jalan 6/6   eff 86%   1.116 m
MO-1077   3 mesin    [05] [07] [10]                   jalan 3/3   eff 84%     684 m
MO-4336   1 mesin    [09]                             jalan 1/1   eff 83%     449 m
```

Pengelompokan dihitung server oleh `group_by_mo()` di `app/source.py`, hasilnya
di field `line.mo_groups`. Tiap MO punya warna pembeda yang dipakai konsisten
di kartu kamera, halaman detail, dan tabel analisa.

### Panel alert

Klik kartu kamera → panel kanan. Kalau AI mendeteksi sesuatu panel berwarna
merah dengan detail deteksi (zona, aktivitas, confidence, snapshot, durasi) dan
tombol **False Alarm** / **Tindak Lanjuti**. Kalau tidak ada deteksi, panel
kosong berwarna hijau dan hanya menampilkan info line.

---

## Struktur

```
cctvdashboard/
├── app/
│   ├── config.py       Konfigurasi (semua via environment variable)
│   ├── models.py       Line / Machine / Camera / Alert / Hall
│   ├── source.py       SimSource (demo) & LiveSource (adapter pabrik)
│   ├── calibration.py  Simpan zona & ROI lampu per kamera
│   └── main.py         FastAPI: routes, WebSocket, background push
├── templates/index.html
├── static/
│   ├── css/style.css
│   └── js/
│       ├── app.js        Render dashboard, ambil /api/lines, subscribe /ws
│       └── calibrate.js  Kalibrasi zona & ROI lampu di atas feed
├── ai/                 Proses terpisah, boleh di mesin lain
│   ├── worker.py         AI worker produksi: CCTV → POST ke dashboard
│   ├── lamp.py           Baca warna lampu tower → status mesin
│   ├── calibrate.py      Alat kalibrasi mandiri (di luar dashboard)
│   ├── webcam_demo.py    Alat uji: webcam / file video sebagai kamera
│   ├── zones.example.json
│   └── requirements.txt
├── deploy/
│   ├── cctv-dashboard.service  systemd dashboard
│   ├── cctv-ai.service         systemd AI worker
│   ├── go2rtc.yaml             restreamer RTSP → browser
│   ├── nginx-cctv.conf         reverse proxy (opsional)
│   └── start-windows.bat
├── data/calibration.json   Dibuat otomatis saat kalibrasi pertama
├── run.py                  Entry point
├── run-demo.sh             Jalankan dashboard dalam mode uji webcam
└── requirements.txt
```

---

## Mulai cepat

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python run.py
```

Buka `http://localhost:8010`. Data masih simulasi (18 line, 180 mesin) sehingga
seluruh tampilan bisa dinilai sebelum kamera dan PLC tersambung.

---

## Instalasi di server pabrik

### Linux

```bash
sudo useradd -r -s /bin/false cctv
sudo mkdir -p /opt/cctvdashboard
sudo cp -r . /opt/cctvdashboard
cd /opt/cctvdashboard
sudo python3 -m venv venv
sudo ./venv/bin/pip install -r requirements.txt
sudo chown -R cctv:cctv /opt/cctvdashboard

sudo cp deploy/cctv-dashboard.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cctv-dashboard
```

Cek: `sudo systemctl status cctv-dashboard` — log: `sudo journalctl -u cctv-dashboard -f`
Akses dari PC lain: `http://<ip-server>:8010`

Buka firewall bila perlu:
```bash
sudo firewall-cmd --add-port=8010/tcp --permanent && sudo firewall-cmd --reload
```

### Windows

Double-click `deploy\start-windows.bat` (venv dibuat otomatis pada run pertama).
Agar jalan saat boot, daftarkan dengan [NSSM](https://nssm.cc):
`nssm install CCTVDashboard`, arahkan ke `venv\Scripts\python.exe` dengan
argumen `run.py`.

---

## Konfigurasi

Semua lewat environment variable.

| Variable | Default | Keterangan |
|---|---|---|
| `CCTV_HOST` | `0.0.0.0` | Alamat bind |
| `CCTV_PORT` | `8010` | Port HTTP |
| `CCTV_PLANT_NAME` | `Weaving Plant` | Nama pabrik di header |
| `CCTV_MESIN_PER_LINE` | `10` | Jumlah mesin per line |
| `CCTV_TARGET_OUTPUT` | `60000` | Target output harian (meter) |
| `CCTV_SOURCE` | `sim` | `sim` = simulasi, `live` = adapter pabrik |
| `CCTV_PUSH_INTERVAL` | `5` | Interval push data ke browser (detik) |
| `CCTV_CALIBRATION_FILE` | `data/calibration.json` | File penyimpanan kalibrasi |
| `CCTV_STREAM_URL` | *(kosong)* | Template URL stream, `{id}` = id line |
| `CCTV_STREAM_URL_GRID` | *(kosong)* | Template sub-stream untuk grid |
| `CCTV_STREAM_MODE` | `video` | `video` / `iframe` / `img` |
| `CCTV_STREAM_IMG_REFRESH` | `2` | Interval refresh (detik) untuk mode `img` |

---

## Menyambung stream CCTV

Browser **tidak bisa** membaca RTSP langsung, jadi perlu restreamer di tengah.
Pakai [go2rtc](https://github.com/AlexxIT/go2rtc) — satu binary, tanpa dependency.

```
CCTV (RTSP) ──► go2rtc ──► HTTP/WebRTC ──► dashboard
                   └─────► RTSP ────────► AI worker
```

Dua jalur terpisah: kalau AI worker mati, kamera tetap terlihat.

**1. Isi daftar kamera** — template di `deploy/go2rtc.yaml`. Nama stream
**harus sama dengan `line_id`** (`rapier-01`, `ajl-01`, ...) karena URL
dibangun dari id itu.

```yaml
streams:
  ajl-01:     rtsp://admin:pass@192.168.1.21:554/Streaming/Channels/101   # main
  ajl-01-sub: rtsp://admin:pass@192.168.1.21:554/Streaming/Channels/102   # sub
```

**2. Arahkan dashboard ke go2rtc:**

```bash
CCTV_STREAM_URL="http://192.168.1.50:1984/api/stream.mp4?src={id}"
CCTV_STREAM_URL_GRID="http://192.168.1.50:1984/api/stream.mp4?src={id}-sub"
CCTV_STREAM_MODE=video
```

`{id}` diganti id line otomatis. Kotak kamera langsung berganti dari
placeholder ke video.

### Pilihan mode

| `CCTV_STREAM_MODE` | URL go2rtc | Latency | Catatan |
|---|---|---|---|
| `video` *(default)* | `/api/stream.mp4?src={id}` | ~1-2 detik | Paling gampang, jalan di semua browser |
| `iframe` | `/stream.html?src={id}` | ~0.3 detik | WebRTC, latency terendah, lebih berat |
| `img` | `/api/frame.jpeg?src={id}` | per-refresh | Snapshot berkala, paling ringan untuk grid besar |

### Kenapa perlu sub-stream

`CCTV_STREAM_URL_GRID` untuk grid (18 kamera sekaligus), `CCTV_STREAM_URL`
untuk halaman detail (1 kamera). Kalau grid ikut memakai main stream 1080p,
18 dekoder video berjalan bersamaan di satu PC operator dan browser akan
tersendat. Sub-stream (biasanya 640x360) menyelesaikan ini.

---

## AI deteksi

AI worker berjalan **terpisah** dari dashboard — boleh di mesin lain yang punya
GPU. Dashboard tidak menjalankan AI sama sekali; ia hanya menerima hasil.

### Pasang

```bash
python3 -m venv venv-ai
./venv-ai/bin/pip install -r ai/requirements.txt
cp ai/zones.example.json ai/zones.json     # lalu sesuaikan
./venv-ai/bin/python ai/worker.py --config ai/zones.json
```

Untuk jalan permanen: `deploy/cctv-ai.service`.

| Opsi | Guna |
|---|---|
| `--config` | File konfigurasi JSON (default `ai/zones.json`) |
| `--model` | Model YOLO. `yolov8n` ringan, `yolov8s/m` lebih akurat |
| `--no-dashboard-calibration` | Abaikan kalibrasi dashboard, pakai `zones.json` saja |
| `--verbose` | Log lebih detail |

### Yang dideteksi

| Deteksi | Cara kerja | Parameter |
|---|---|---|
| **Operator tidak di area** | YOLO person, titik **kaki** di dalam zona; tidak ada orang > N detik | `absent_seconds` (120) |
| **Kerumunan di line** | ≥ N orang di zona > M detik | `crowd_min` (3), `crowd_seconds` (30) |
| **Mesin stop tanpa penanganan** | Ada lampu tower merah > N detik **dan** tidak ada orang di zona | `response_seconds` (300) |
| **Status tiap mesin** | Warna lampu tower dibaca per mesin, dikirim ke dashboard | `status_interval` (10) |

Titik acuan orang adalah **tengah-bawah** bounding box, bukan pusatnya —
posisi kaki di lantai yang menentukan orang itu di dalam zona atau bukan.

### Membaca status mesin dari lampu tower

Deteksi lampu **tidak memakai AI** — hanya analisa warna (HSV) di dalam ROI.
Jauh lebih murah dan lebih akurat daripada deteksi objek, dan satu kamera bisa
membaca **10 lampu sekaligus** tanpa tambahan beban berarti.

| Warna lampu | Status mesin |
|---|---|
| Hijau | `run` — jalan |
| Kuning | `idle` — setting / peringatan |
| Merah | `stop` — berhenti / alarm |
| Padam | `off` — mati |

Worker mengirimnya ke `POST /api/lines/{id}/machines`, dan status 10 mesin di
dashboard langsung mengikuti apa yang terlihat kamera — termasuk kolom Status
di tabel Analysis dan kotak mesin di view Map.

Ini deteksi yang paling bernilai: menghasilkan angka *response time* operator
terhadap mesin berhenti, sesuatu yang tidak dicatat sistem mesin.

**Penting:** begitu satu line menerima data dari AI worker, simulator berhenti
menimpa line tersebut selama 60 detik (`BaseSource.VISION_LEASE`). Tanpa ini,
data asli tertimpa data simulasi tiap 5 detik. Line lain tetap disimulasi, jadi
bisa dicampur: sebagian line pakai kamera asli, sisanya simulasi.

### Beban komputasi

Worker melakukan **sampling 3 fps**, bukan 30 fps — deteksi orang tidak butuh
setiap frame, dan ini yang membuat banyak kamera muat di satu GPU. Frame
di-`grab()` lalu dibuang tanpa decode kecuali saat gilirannya diproses.

Perkiraan kasar dengan `yolov8n` di GPU kelas RTX 3060: ~15-20 kamera pada
3 fps. Tanpa GPU (CPU saja): 3-5 kamera.

### Anti-spam

Tiap jenis alert punya `cooldown_seconds` (default 600) per kamera, supaya satu
kejadian tidak mengirim puluhan alert. Alert dari AI **tidak hilang sendiri** —
hanya tertutup saat operator menekan Tindak Lanjuti / False Alarm di dashboard.

---

## Kalibrasi zona & ROI lampu

Yang paling menentukan akurasi adalah **zona** (area line di dalam frame) dan
**ROI lampu** (kotak di sekeliling tiap lampu tower). Tanpa zona, orang yang
lewat di gang ikut terhitung.

### Cara utama: langsung di dashboard

Buka kartu kamera → tombol **Kalibrasi** di kanan atas. Gambar dengan mouse
langsung di atas feed kamera, tekan **Simpan**.

1. **Zona Line** — klik sudut-sudut area line.
2. **Kotak Lampu** — seret **satu kotak panjang** menutupi semua lampu tower,
   isi jumlah mesin, tekan **Bagi Rata**. Sepuluh ROI langsung terbentuk.
   (Bisa juga satu per satu; klik kotak untuk menghapus.)
3. Panel di bawah toolbar membaca **warna lampu langsung di browser** —
   `03 red 87%` berarti ROI mesin 3 memang menangkap lampu merah. Ini yang
   membedakan dengan menebak angka: hasilnya terverifikasi seketika.
4. **Simpan** → tersimpan ke `data/calibration.json`.

Tiga tombol yang mudah tertukar:

| Tombol | Yang terjadi |
|---|---|
| **Batal Terakhir** | Hapus satu titik zona / satu kotak terakhir di kanvas |
| **Bersihkan Gambar** | Kosongkan kanvas. Data di server **belum** berubah sampai Simpan ditekan |
| **Hapus Kalibrasi** | Hapus data tersimpan untuk **kamera ini saja**. Kamera lain tidak tersentuh |

**AI worker menariknya sendiri tiap 30 detik** lewat
`GET /api/lines/{id}/calibration`. Jadi menggeser zona di dashboard langsung
berlaku pada kamera RTSP aslinya — tanpa menyalin file ke server AI, tanpa
me-restart worker. `zones.json` tetap dipakai kalau line belum pernah
dikalibrasi atau dashboard sedang mati.

**Tidak ada kalibrasi bawaan.** Kalau `data/calibration.json` belum ada, semua
kamera mulai kosong. Kalibrasi yang sudah disimpan bertahan sampai dihapus —
restart dashboard tidak menyentuhnya, dan menyimpan satu kamera tidak mengubah
kamera lain.

Menghapus dari terminal:

```bash
curl -X DELETE http://localhost:8010/api/lines/ajl-01/calibration   # satu kamera
rm data/calibration.json                                            # semuanya
```

### Catatan penting

> **URL RTSP tidak disimpan di dashboard.** Di dalamnya ada password kamera,
> sedangkan dashboard dibuka banyak orang di jaringan pabrik. Yang disimpan
> hanya koordinat zona dan ROI. URL kamera tetap di `zones.json` milik worker.

> **Koordinat selalu persen (0-100), bukan piksel** — tidak perlu dikalibrasi
> ulang kalau resolusi kamera diubah.

> **Satu sumber kalibrasi.** `ai/webcam_demo.py` juga menarik kalibrasi dari
> dashboard tiap 10 detik, sama seperti `ai/worker.py`. Tanpa ini, overlay yang
> dibakar ke frame memakai `--zone/--lamp` dari CLI sementara dashboard
> menyimpan yang lain — dua-duanya tampil bertumpuk dan menyesatkan.

> **Pratinjau warna di browser** butuh header `Access-Control-Allow-Origin` dari
> sumber stream. Kalau tidak ada, kotaknya tetap tersimpan dan tetap dibaca AI
> worker — hanya pratinjau warnanya yang mati, dan panel mengatakan demikian
> (bukan diam-diam menampilkan "padam").

> Ambang batas HSV di `static/js/calibrate.js` sengaja disamakan dengan
> `ai/lamp.py`. Kalau salah satu diubah, ubah keduanya.

### Alat kalibrasi mandiri (opsional)

Kalau dashboard belum jalan, atau hanya punya foto kamera:

```bash
./venv-ai/bin/python ai/calibrate.py --source rtsp://192.168.1.50:8554/ajl-01 --line-id ajl-01
./venv-ai/bin/python ai/calibrate.py --device 1 --line-id ajl-01        # webcam
./venv-ai/bin/python ai/calibrate.py --source foto-ajl01.jpg            # dari foto
```

Buka `http://127.0.0.1:1985` — cara pakainya sama, hasilnya disalin ke
`ai/zones.json` (atau tombol Simpan → `ai/zones.generated.json`).

### Bentuk konfigurasi

```json
{
  "line_id": "ajl-01",
  "rtsp": "rtsp://192.168.1.50:8554/ajl-01",
  "zone": [[8,30],[92,30],[92,95],[8,95]],
  "machines": [
    {"no": 1, "lamp": [5.4, 4.0, 6.4, 14.0]},
    {"no": 2, "lamp": [14.6, 4.0, 6.4, 14.0]}
  ]
}
```

---

## Menyambung ke data produksi asli

Data sekarang masih **simulasi** (`CCTV_SOURCE=sim`). Untuk data pabrik, isi
dua method di `app/source.py` → class `LiveSource`:

```python
class LiveSource(BaseSource):
    def bootstrap(self):
        # dipanggil sekali saat startup: bangun daftar Group + Line + Machine,
        # posisi di denah, dan info kamera

    def refresh(self):
        # dipanggil tiap CCTV_PUSH_INTERVAL detik: update status / rpm / eff /
        # output / stops / order_mo tiap MESIN, lalu panggil rollup()
```

Lalu set `CCTV_SOURCE=live`. Struktur `Line`/`Machine` di `app/models.py` tidak
perlu diubah — frontend otomatis ikut. Kalau adapter belum diisi, server gagal
start dengan pesan jelas, bukan diam-diam kosong.

Sumber yang umum dipakai: database loom monitoring, tag OPC-UA / Modbus dari
PLC, atau REST API vendor mesin.

**Saran pembagian tugas:** angka produksi (RPM, output, efisiensi) sebaiknya
dari PLC, bukan dari AI. AI dipakai untuk yang tidak dicatat mesin: kehadiran
operator dan response time. Kombinasi keduanya yang membuat dashboard berguna —
*"AJL 03 stop 12 menit, operator baru muncul di menit ke-9"*.

---

## Menguji tanpa CCTV

`ai/webcam_demo.py` menirukan go2rtc + AI worker sekaligus dalam satu proses:
ambil frame, deteksi orang, baca lampu, gambar overlay, layani MJPEG, dan kirim
alert ke dashboard.

Satu proses karena macOS hanya mengizinkan **satu** aplikasi membuka webcam pada
satu waktu — kalau bridge dan worker jalan terpisah, keduanya berebut.

### Dari webcam laptop

```bash
python3 -m venv venv-ai
./venv-ai/bin/pip install -r ai/requirements.txt
./venv-ai/bin/python ai/webcam_demo.py --dashboard http://127.0.0.1:8010 --line-id ajl-01
```

Dashboard di terminal lain:

```bash
./run-demo.sh
```

(setara dengan `CCTV_STREAM_MODE=img CCTV_STREAM_URL="http://127.0.0.1:1984/frame?src={id}" python run.py`.
Port lain: `CCTV_PORT=8010 ./run-demo.sh`)

Berdiri di depan kamera lalu menyingkir > 15 detik → alert "Operator tidak di
area" muncul di dashboard.

Default `--device 1` (webcam fisik). Index `0` sering dipakai OBS Virtual
Camera — sesuaikan dengan `--device 0` kalau OBS tidak terpasang.

### Dari file video

Berguna kalau punya rekaman CCTV lama dari pabrik:

```bash
./venv-ai/bin/python ai/webcam_demo.py --source rekaman.mp4 --loop
```

### Opsi

| Opsi | Default | Guna |
|---|---|---|
| `--device` | `1` | Index webcam |
| `--source` | *(webcam)* | URL RTSP/HTTP atau path file video |
| `--loop` | — | Ulangi dari awal kalau sumbernya file video |
| `--detector` | `yolo` | `hog` = detektor bawaan OpenCV, tanpa unduh model |
| `--zone` | tengah frame | Polygon zona dalam persen: `x1,y1,x2,y2,...` |
| `--lamp` | — | ROI lampu satu mesin: `x,y,w,h`. Ulangi untuk tiap mesin |
| `--lamp-grid` | — | Buat N ROI lampu otomatis berderet: `--lamp-grid 10` |
| `--absent` | `15` | Detik tanpa orang sebelum alert (produksi: 120) |
| `--line-id` | `ajl-01` | Line tujuan alert |
| `--fps` | `4` | fps deteksi |
| `--max-width` | `960` | Perkecil frame setelah ditangkap (hemat CPU) |
| `--no-overlay` | — | Jangan bakar zona & kotak ke frame |
| `--no-dashboard-calibration` | — | Abaikan kalibrasi dashboard, pakai `--zone/--lamp` |
| `--probe` | — | Cek sumber lalu keluar: resolusi, kecerahan, jumlah deteksi |

Endpoint bridge: `/frame` (1 JPEG), `/stream` (MJPEG), `/status` (jumlah orang,
fps, dan pembacaan lampu).

> Ini **alat uji**. Untuk produksi tetap pakai go2rtc + `ai/worker.py` — MJPEG
> dari satu proses Python tidak akan kuat melayani 18 kamera.

### Kalau frame hitam

Jalankan `--probe` dulu: ia menampilkan resolusi dan **kecerahan** tiap frame.
Di bawah 6 berarti frame hitam.

```bash
./venv-ai/bin/python ai/webcam_demo.py --probe
```

> **Jangan paksa resolusi kamera** dengan `--width/--height`. Di macOS, meminta
> ukuran yang tidak didukung webcam membuat AVFoundation mengirim frame hitam —
> lampu kamera menyala tapi gambarnya kosong. Biarkan kamera memakai resolusi
> nativenya; frame diperkecil setelah ditangkap lewat `--max-width`.

> **macOS:** jalankan dari aplikasi Terminal Anda sendiri, bukan lewat editor
> atau agent. Izin kamera hanya bisa diberikan lewat dialog sistem yang muncul
> untuk aplikasi yang menjalankannya. Kalau dialog tidak muncul: System Settings
> → Privacy & Security → Camera, izinkan Terminal, lalu tutup dan buka lagi
> Terminal (izin baru berlaku setelah restart).

Penyebab frame hitam lainnya: OBS masih memegang kamera (tutup OBS termasuk
Virtual Camera-nya), atau salah index device.

### Soal URL RTSP publik

URL demo publik lama (Wowza `wowzaec2demo.streamlock.net`, IPVM, traffic cam)
sudah mati semua. Perlu diperhatikan juga: URL Wowza Cloud berbentuk
`xxx.entrypoint.cloud.wowza.com:1935` adalah endpoint **ingest** — tempat
mengirim video, bukan menonton. RTSP DESCRIBE akan berhasil, tapi SETUP membalas
`403 Forbidden`. URL untuk menonton ada di dashboard Wowza, biasanya HLS
`.m3u8` lewat CDN mereka.

---

## API

| Endpoint | Keterangan |
|---|---|
| `GET /` | Dashboard |
| `GET /api/lines` | Snapshot semua line + mesin + denah + kalibrasi + KPI |
| `GET /api/lines/{id}` | Detail satu line |
| `GET /api/summary` | Ringkasan KPI saja |
| `GET /api/alerts` | Daftar alert AI yang aktif |
| `POST /api/alerts` | **Dipanggil AI worker** saat kamera mendeteksi sesuatu |
| `POST /api/alerts/{id}/resolve` | Operator menekan Tindak Lanjuti / False Alarm |
| `POST /api/lines/{id}/machines` | **Status mesin dari pembacaan lampu tower** |
| `GET /api/calibration` | Semua kalibrasi (dipakai AI worker) |
| `GET/PUT/DELETE /api/lines/{id}/calibration` | Kalibrasi satu kamera |
| `GET /healthz` | Health check (untuk monitoring server) |
| `GET /api/docs` | Dokumentasi API otomatis (Swagger) |
| `WS /ws` | Push data realtime |

### Mengirim deteksi dari AI worker

```bash
curl -X POST http://localhost:8010/api/alerts \
  -H "Content-Type: application/json" \
  -d '{"line_id":"ajl-01","label":"Operator tidak di area","confidence":91,
       "activity":"Tidak ada orang di zona line","object_type":"Person",
       "duration":143,"severity":"high"}'
```

Alert langsung muncul di semua dashboard yang terbuka. Endpoint ini bekerja di
mode `sim` juga, jadi bisa diuji sebelum kamera terpasang.

### Mengirim status mesin

```bash
curl -X POST http://localhost:8010/api/lines/ajl-01/machines \
  -H "Content-Type: application/json" \
  -d '{"machines":[{"no":1,"status":"run","color":"green"},
                   {"no":3,"status":"stop","color":"red"}]}'
```

---

## Catatan operasional

- **Jalankan 1 worker uvicorn saja.** State line disimpan di memori dan
  WebSocket butuh koneksi sticky. Kalau nanti butuh multi-worker, pindahkan
  state ke Redis atau database dulu.
- **Reverse proxy wajib meneruskan header WebSocket.** Config nginx yang sudah
  benar ada di `deploy/nginx-cctv.conf`. Ini penyebab paling sering dashboard
  "diam" setelah dipasang proxy.
- Frontend menyambung ulang otomatis kalau koneksi putus (backoff 2–12 detik).
  Titik di kiri atas header: hijau = terhubung, abu = terputus.
- Update dari server **tidak membangun ulang DOM** — nilai di-patch di tempat
  supaya tidak berkedip dan scroll/hover tidak hilang. `render()` hanya dipanggil
  saat struktur berubah (ganti view, filter, buka accordion).
- AI worker tidak mati kalau dashboard belum siap; ia mencatat warning lalu
  mencoba lagi. Dashboard boleh di-restart tanpa mematikan semua worker.
- Alert simulasi hilang sendiri setelah 4–10 siklus. Alert dari AI worker
  **tidak** — hanya tertutup saat operator menanganinya.
