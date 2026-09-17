#!/usr/bin/env bash
# Jalankan dashboard dalam mode uji webcam.
# Pakai bersama: ./venv-ai/bin/python ai/webcam_demo.py
set -e
cd "$(dirname "$0")"

export CCTV_PORT="${CCTV_PORT:-8000}"
export CCTV_STREAM_MODE="${CCTV_STREAM_MODE:-img}"

# BAWAANNYA TARIK-SATU-GAMBAR (/frame), BUKAN MJPEG BERSAMBUNG (/stream).
#
# MJPEG jauh lebih mulus (~23 fps) tetapi menahan satu koneksi TCP per kamera
# selamanya. Browser membatasi ~6 koneksi bersamaan per host, jadi dari 18
# kotak kamera hanya 6 yang pernah termuat dan 12 sisanya hitam selamanya.
# Terukur: 6 dari 18. Ini BUKAN keterbatasan server — 18 permintaan serentak
# lewat curl semuanya berhasil dengan CPU 1,1 inti.
#
# Dengan /frame + penyegaran 1 detik, koneksinya dilepas tiap tarikan, jadi
# ke-18 kamera tampil. Harganya: gambar berganti 1 detik sekali.
#
# Untuk demo MULUS pada beberapa kamera saja:
#   CCTV_STREAM_URL="http://127.0.0.1:1984/stream?src={id}" \
#     CCTV_STREAM_IMG_REFRESH=0 ./run-demo.sh
#
# JANGAN pakai ${VAR:-default} untuk URL di bawah: nilainya mengandung {id},
# dan "}" pertama menutup ekspansi lebih awal sehingga "}" sisanya ikut
# tertempel di ujung URL.
if [ -z "${CCTV_STREAM_URL:-}" ]; then
  CCTV_STREAM_URL="http://127.0.0.1:1984/frame?src={id}"
fi
export CCTV_STREAM_URL
export CCTV_STREAM_IMG_REFRESH="${CCTV_STREAM_IMG_REFRESH:-1}"

echo "Dashboard  : http://localhost:$CCTV_PORT"
echo "Sumber feed: http://127.0.0.1:1984 (ai/webcam_demo.py)"
echo
exec ./venv/bin/python run.py
