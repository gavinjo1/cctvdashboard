# Checklist Go-Live

Semua titik yang perlu diubah sudah ditandai di dalam kode. Untuk melihat
daftarnya:

```bash
grep -rn "TODO(pabrik)" app/ ai/ templates/ static/
```

Dashboard juga memeriksa dirinya sendiri saat start. Kalau ada yang belum
siap, log akan menampilkan peringatan besar, dan `GET /healthz` mengembalikan
`"production_ready": false` beserta daftar masalahnya — pakai ini untuk
monitoring.

---

## A. Wajib

### 1. Sumber data produksi
| Berkas | Yang harus diisi |
|---|---|
| `app/source.py` → `LiveSource.bootstrap()` | Daftar line & mesin dari master data |
| `app/source.py` → `LiveSource.refresh()` | Status/RPM/efisiensi/output tiap mesin |
| `app/source.py` → `LAYOUT` | Jumlah line per jenis mesin |
| `app/source.py` → `OPERATORS` | Hapus; ambil dari roster |

Lalu set `CCTV_SOURCE=live`.

**Perhatian pada `output`:** nilai yang dikirim harus akumulasi **sejak awal
periode** (shift/hari), bukan sejak mesin dipasang. Dashboard menolkan
penghitung saat pergantian shift; kalau sumber tetap mengirim akumulasi total,
angka akan melompat kembali setelah reset.

### 2. Keamanan
```bash
python -c "import secrets;print(secrets.token_urlsafe(32))"
```
Isi hasilnya ke `CCTV_API_KEY` **dan** ke `api_key` di `ai/zones.json`.

Tanpa ini siapa pun di jaringan pabrik bisa mengirim alert palsu dan mengubah
status mesin.

Jangan mengisinya `false` atau `off` untuk mematikannya — nilai itu **menjadi
kuncinya**, dan pemeriksaan kesiapan ikut mengira kunci sudah diisi. Untuk
mematikan, kosongkan. Sejak sekarang nilai lemah seperti ini ditolak oleh
pemeriksaan startup dan muncul di `/healthz`.

#### Endpoint yang masih terbuka

`CCTV_API_KEY` hanya melindungi dua endpoint yang dipanggil AI worker
(`POST /api/alerts`, `POST /api/lines/{id}/machines`). Endpoint yang dipanggil
dari browser **tidak diperiksa sama sekali**:

| Endpoint | Akibat bila disalahgunakan |
|---|---|
| `PUT /api/lines/{id}/calibration` | Zona digeser — line berhenti terpantau tanpa terlihat |
| `DELETE /api/lines/{id}/calibration` | Kalibrasi satu kamera hilang, harus diulang dari nol |
| `DELETE /api/log/{id}` | Catatan deteksi terhapus |
| `POST /api/alerts/{id}/resolve` | Alert ditutup atas nama operator mana pun |

Siapa pun yang bisa menjangkau dashboard bisa melakukannya dengan satu baris:

```bash
curl -X DELETE http://SERVER:8000/api/lines/ajl-01/calibration
```

API key tidak bisa dipakai di sini: nilainya harus ikut terkirim ke setiap
browser yang membuka dashboard, yang sama saja dengan mengumumkannya. Yang
dibutuhkan adalah sesi/login, bukan kunci mesin.

**Keputusan saat ini: dibiarkan terbuka**, dengan andalan jaringan pabrik yang
terisolasi. Yang **wajib** dipastikan selama itu:

- Dashboard **tidak** dapat dijangkau dari luar jaringan pabrik (periksa
  firewall dan rute VPN, bukan hanya asumsi)
- `data/calibration.json` **ikut backup harian** — ini satu-satunya pengaman
  terhadap penghapusan kalibrasi, disengaja maupun tidak

Tinjau ulang sebelum dashboard dijangkau jaringan kantor, Wi-Fi tamu, atau
internet. Pilihan yang tersedia, dari paling ringan: basic-auth nginx di depan
seluruh dashboard; kata sandi terpisah khusus tindakan merusak; atau login
sungguhan di aplikasi (sekaligus membuat nama operator di jejak audit
terverifikasi, bukan sekadar diketik).

Isi juga `data/operators.json` dengan nama yang boleh menutup alert:
```json
["Budi S.", "Rina M.", "Sutrisno"]
```
Kalau file ini tidak ada, nama bebas diketik — tetap dicatat, tapi tidak
tervalidasi.

### 3. Shift & reset
| Variable | Isi |
|---|---|
| `CCTV_SHIFT_STARTS` | `06:00,14:00,22:00` |
| `CCTV_SHIFT_NAMES` | `Shift 1,Shift 2,Shift 3` |
| `CCTV_RESET_MODE` | `shift` atau `day` |
| `CCTV_DAY_RESET_AT` | dipakai kalau mode `day` |

Shift yang melewati tengah malam sudah ditangani. Angka periode lama otomatis
tersimpan ke database sebelum dinolkan.

### 4. Stream kamera
- Pasang go2rtc, isi semua kamera (main + sub-stream). Nama stream **harus
  sama** dengan `line_id`.
- `CCTV_STREAM_URL`, `CCTV_STREAM_URL_GRID`, `CCTV_STREAM_MODE`
- `cam.online` harus diperbarui dari status NVR di `LiveSource.refresh()`

---

## B. Penting

### 5. Kalibrasi kamera
Satu per satu lewat tombol **Kalibrasi** di halaman detail kamera.
Lakukan saat shift jalan supaya ada lampu hijau, kuning, dan merah untuk
diverifikasi sekaligus.

### 6. Ambang batas AI (`ai/zones.json`)
| Parameter | Bawaan | Pertimbangan |
|---|---|---|
| `absent_seconds` | 120 | Berapa lama operator boleh meninggalkan line |
| `response_seconds` | 300 | Target response time mesin stop |
| `crowd_min` | 3 | Berapa orang dianggap kerumunan |
| `cooldown_seconds` | 600 | Cegah banjir alert |
| `lamp_min_ratio` | 0.10 | **Pasti perlu disetel** di pencahayaan asli |

### 7. Denah pabrik
`HALLS` di `app/source.py` masih karangan. Ukur tata letak asli.

### 8. Definisi efisiensi
Sekarang rata-rata sederhana dari mesin. Samakan dengan rumus bagian produksi
(mis. pick actual / pick teoretis), kalau tidak angkanya akan dibantah.

### 9. Zona waktu
Server, AI worker, dan NVR harus satu zona waktu dan tersinkron NTP. Kalau
tidak, waktu di alert tidak cocok dengan rekaman CCTV dan alert tidak bisa
diverifikasi.

```bash
sudo timedatectl set-timezone Asia/Jakarta
timedatectl status | grep -i synchronized
```

---

## C. Deployment

- **Tetap 1 worker uvicorn.** State di memori + WebSocket butuh koneksi sticky.
- nginx **wajib** meneruskan header `Upgrade`/`Connection`
  (`deploy/nginx-cctv.conf` sudah benar)
- Buka port di firewall
- Backup **`data/`** — berisi kalibrasi dan seluruh histori
- `chmod 600 ai/zones.json` — berisi password kamera
- Hapus dari server produksi: `ai/webcam_demo.py`, `run-demo.sh`,
  `deploy/start-windows.bat` (kalau Linux)

### Kapasitas GPU
`yolov8n` di RTX 3060 ≈ 15–20 kamera pada 3 fps. CPU saja hanya 3–5 kamera.

---

## D. Menyusul

- **Alert keluar dari layar** — buzzer/andon/Telegram + eskalasi kalau tidak
  ditanggapi. Tanpa ini alert hanya berguna kalau ada yang menatap layar.
- **Laporan** — data sudah terkumpul di `data/history.db`, tinggal dibuatkan
  tampilannya. Endpoint yang sudah ada: `/api/history/production`,
  `/api/history/alerts`, `/api/history/alert-stats`, `/api/shift/summary`
- **Monitoring kamera mati** — kegagalan paling berbahaya: dashboard terlihat
  normal padahal buta

---

## Verifikasi sebelum serah terima

```bash
curl -s http://SERVER:8000/healthz | python -m json.tool
```

`"production_ready": true` dan `warnings: []` berarti pemeriksaan otomatis
sudah lolos. Yang tidak bisa diperiksa otomatis: kalibrasi kamera, ambang
batas AI, denah, dan definisi efisiensi — itu perlu diperiksa orang.
