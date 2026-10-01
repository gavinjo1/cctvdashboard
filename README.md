# cctvdashboard

Dashboard monitoring CCTV + produksi untuk pabrik weaving.
Status mesin dibaca dari lampu menara lewat kamera.

## Pasang

Butuh Python 3.12.

```bash
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
```

```bash
python3 -m venv venv-ai && ./venv-ai/bin/pip install -r ai/requirements.txt
```

## Menjalankan

Butuh 3 terminal, dijalankan berurutan.

```bash
./run-demo.sh
```

```bash
./venv-ai/bin/python ai/foto_kamera.py --dir ai/foto --lacak
```

```bash
./venv-ai/bin/python ai/worker.py --config ai/zones.json
```

Buka http://localhost:8000

Tanpa terminal ketiga, lampu tidak terbaca dan semua mesin abu-abu.

## Menghentikan

```bash
pkill -f foto_kamera.py; pkill -f "ai/worker.py"; pkill -f run.py
```

## Kalau laptop panas

`--lacak` tanpa nama line melacak semua video. Sebutkan line yang perlu saja:

```bash
./venv-ai/bin/python ai/foto_kamera.py --dir ai/foto --lacak ajl-01,ajl-03
```

Atau hapus `--lacak`. Lampu tetap terbaca, hanya kotak operator yang hilang.

## Kalau mau video mulus

Bawaannya tarik-satu-gambar tiap 1 detik supaya semua kamera tampil. Untuk
6 kamera atau kurang, MJPEG jauh lebih mulus:

```bash
CCTV_STREAM_URL="http://127.0.0.1:1984/stream?src={id}" CCTV_STREAM_IMG_REFRESH=0 ./run-demo.sh
```

Lebih dari 6 kamera, yang ke-7 dan seterusnya hitam.

## Mengatur line yang tampil

Beri tanda `#` di depan baris yang tidak dipakai di `ai/foto/peta.txt`:

```
ajl-01 = vid11.mp4
#ajl-07 = vid1.mp4
```

## Menguji

```bash
./venv-ai/bin/python ai/uji_worker.py
./venv-ai/bin/python ai/uji_warna_lampu.py
./venv/bin/python app/uji_eventlog.py
```

Rantai penuh lewat HTTP, butuh `./run-demo.sh` jalan:

```bash
./venv/bin/python app/uji_rantai.py
```

## Memperbarui

```bash
git pull
./venv/bin/pip install -r requirements.txt
./venv-ai/bin/pip install -r ai/requirements.txt
```
