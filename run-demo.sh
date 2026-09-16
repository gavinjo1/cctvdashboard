#!/usr/bin/env bash
# Jalankan dashboard dalam mode uji webcam.
# Pakai bersama: ./venv-ai/bin/python ai/webcam_demo.py
set -e
cd "$(dirname "$0")"

export CCTV_PORT="${CCTV_PORT:-8000}"
export CCTV_STREAM_MODE="${CCTV_STREAM_MODE:-img}"
# MJPEG bersambung, bukan tarik-satu-gambar. Di mode "img" app.js memakai
# Math.max(1, IMG_REFRESH) — jadi 1 fps adalah lantai keras. Dengan /stream
# dan IMG_REFRESH=0, penyegaran JS dimatikan dan <img> menahan koneksi
# sendiri: mulus di ~23 fps. Berlaku untuk foto_kamera.py maupun webcam_demo.py.
# JANGAN pakai ${VAR:-default} di sini: nilai bawaannya mengandung {id},
# dan "}" pertama menutup ekspansi lebih awal sehingga "}" sisanya ikut
# tertempel di ujung URL.
if [ -z "${CCTV_STREAM_URL:-}" ]; then
  CCTV_STREAM_URL="http://127.0.0.1:1984/stream?src={id}"
fi
export CCTV_STREAM_URL
export CCTV_STREAM_IMG_REFRESH="${CCTV_STREAM_IMG_REFRESH:-0}"

echo "Dashboard  : http://localhost:$CCTV_PORT"
echo "Sumber feed: http://127.0.0.1:1984 (ai/webcam_demo.py)"
echo
exec ./venv/bin/python run.py
