"""Konfigurasi aplikasi. Semua bisa dioverride lewat environment variable."""
import os


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


class Settings:
    # --- server ---
    HOST = os.getenv("CCTV_HOST", "0.0.0.0")
    PORT = _int("CCTV_PORT", 8000)

    # --- identitas pabrik (tampil di header) ---
    PLANT_NAME = os.getenv("CCTV_PLANT_NAME", "Weaving Plant")

    # --- parameter produksi ---
    MESIN_PER_LINE = _int("CCTV_MESIN_PER_LINE", 10)
    TARGET_OUTPUT = _int("CCTV_TARGET_OUTPUT", 60000)   # meter / hari

    # --- file penyimpanan kalibrasi kamera (zona & ROI lampu) ---
    CALIBRATION_FILE = os.getenv("CCTV_CALIBRATION_FILE", "data/calibration.json")

    # --- sumber data: "sim" (simulasi) atau "live" (adapter pabrik) ---
    SOURCE = os.getenv("CCTV_SOURCE", "sim").lower()

    # --- interval push data ke browser (detik) ---
    PUSH_INTERVAL = _int("CCTV_PUSH_INTERVAL", 5)

    # --- stream kamera ---------------------------------------------------
    # Template URL restreamer. {id} diganti id line (mis. "ajl-01").
    # Kosong = kotak kamera memakai placeholder (mode demo).
    #
    # Contoh go2rtc:
    #   MP4/MSE  : http://192.168.1.50:1984/api/stream.mp4?src={id}
    #   WebRTC   : http://192.168.1.50:1984/stream.html?src={id}   (mode=iframe)
    #   MJPEG    : http://192.168.1.50:1984/api/frame.jpeg?src={id} (mode=img)
    STREAM_URL = os.getenv("CCTV_STREAM_URL", "")

    # URL untuk grid (banyak kamera sekaligus) — pakai sub-stream resolusi rendah.
    # Kosong = pakai STREAM_URL yang sama.
    STREAM_URL_GRID = os.getenv("CCTV_STREAM_URL_GRID", "")

    # Cara render: "video" (<video>, untuk MP4/HLS), "iframe" (player WebRTC
    # bawaan go2rtc), atau "img" (MJPEG / snapshot berkala).
    STREAM_MODE = os.getenv("CCTV_STREAM_MODE", "video").lower()

    # Interval refresh untuk mode "img" (detik). 0 = tidak di-refresh.
    STREAM_IMG_REFRESH = _int("CCTV_STREAM_IMG_REFRESH", 2)

    # kompatibilitas lama
    STREAM_BASE = os.getenv("CCTV_STREAM_BASE", "")


settings = Settings()
