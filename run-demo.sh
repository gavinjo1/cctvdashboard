#!/usr/bin/env bash
# Jalankan dashboard dalam mode uji webcam.
# Pakai bersama: ./venv-ai/bin/python ai/webcam_demo.py
set -e
cd "$(dirname "$0")"

export CCTV_PORT="${CCTV_PORT:-8000}"
export CCTV_STREAM_MODE=img
export CCTV_STREAM_URL="http://127.0.0.1:1984/frame?src={id}"
export CCTV_STREAM_IMG_REFRESH=1

echo "Dashboard  : http://localhost:$CCTV_PORT"
echo "Sumber feed: http://127.0.0.1:1984 (ai/webcam_demo.py)"
echo
exec ./venv/bin/python run.py
