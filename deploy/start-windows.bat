@echo off
REM Server pabrik Windows. Taruh file ini di folder aplikasi.
cd /d "%~dp0.."

set CCTV_HOST=0.0.0.0
set CCTV_PORT=8010
set CCTV_PLANT_NAME=Weaving Plant
set CCTV_SOURCE=sim
set CCTV_PUSH_INTERVAL=5

if not exist venv (
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
)

python run.py
pause
