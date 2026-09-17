"""Konfigurasi aplikasi.

Semua nilai bisa dioverride lewat environment variable — lihat
`deploy/cctv-dashboard.service` untuk contoh pengisiannya di server.

Cari `TODO(pabrik)` di seluruh proyek untuk melihat semua yang WAJIB
disesuaikan sebelum dipakai produksi:

    grep -rn "TODO(pabrik)" app/ ai/ static/ templates/
"""
import os


def _int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _bool(name, default=False):
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on", "ya")


def _csv(name, default=""):
    raw = os.getenv(name, default)
    return [x.strip() for x in raw.split(",") if x.strip()]


class Settings:
    # ==================================================================
    # SERVER
    # ==================================================================
    HOST = os.getenv("CCTV_HOST", "0.0.0.0")
    PORT = _int("CCTV_PORT", 8000)

    # TODO(pabrik): ganti dengan nama pabrik sebenarnya.
    PLANT_NAME = os.getenv("CCTV_PLANT_NAME", "Weaving Plant")

    # TODO(pabrik): zona waktu server, AI worker, dan NVR HARUS sama.
    # Kalau berbeda, waktu di alert tidak akan cocok dengan rekaman CCTV
    # dan alert jadi tidak bisa diverifikasi. Pastikan NTP aktif juga.
    TIMEZONE = os.getenv("CCTV_TIMEZONE", "Asia/Jakarta")

    # ==================================================================
    # PARAMETER PRODUKSI
    # ==================================================================
    # TODO(pabrik): pastikan semua line memang berisi jumlah mesin ini.
    # Kalau tidak seragam, isi per-line lewat LiveSource.bootstrap().
    MESIN_PER_LINE = _int("CCTV_MESIN_PER_LINE", 10)

    # TODO(pabrik): target output harian sebenarnya, dalam satuan yang
    # dipakai bagian produksi (meter / yard / pick). Ganti juga label
    # satuannya di templates/index.html kalau bukan meter.
    TARGET_OUTPUT = _int("CCTV_TARGET_OUTPUT", 60000)

    # ==================================================================
    # SHIFT & RESET HARIAN
    # ==================================================================
    # TODO(pabrik): jam mulai tiap shift, format "HH:MM", urut naik.
    # Contoh 3 shift 8 jam: "06:00,14:00,22:00"
    SHIFT_STARTS = _csv("CCTV_SHIFT_STARTS", "06:00,14:00,22:00")

    # TODO(pabrik): nama shift, jumlahnya harus sama dengan SHIFT_STARTS.
    SHIFT_NAMES = _csv("CCTV_SHIFT_NAMES", "Shift 1,Shift 2,Shift 3")

    # Kapan penghitung output/stop dinolkan:
    #   "shift" = tiap pergantian shift
    #   "day"   = sekali sehari pada DAY_RESET_AT
    # TODO(pabrik): samakan dengan cara bagian produksi menghitung.
    RESET_MODE = os.getenv("CCTV_RESET_MODE", "shift").lower()
    DAY_RESET_AT = os.getenv("CCTV_DAY_RESET_AT", "06:00")

    # ==================================================================
    # PENYIMPANAN
    # ==================================================================
    # File kalibrasi kamera (zona & ROI lampu).
    # TODO(pabrik): file ini WAJIB ikut backup. Hilang = kalibrasi ulang
    # semua kamera dari nol.
    CALIBRATION_FILE = os.getenv("CCTV_CALIBRATION_FILE", "data/calibration.json")
    #: Penugasan MO per mesin, diisi lewat portal MO di dashboard.
    MO_FILE = os.getenv("CCTV_MO_FILE", "data/mo.json")

    # Database histori: produksi per jam, log alert, jejak audit.
    # Tanpa ini, semua data hilang setiap dashboard di-restart.
    DB_FILE = os.getenv("CCTV_DB_FILE", "data/history.db")

    # Berapa lama histori disimpan (hari). 0 = selamanya.
    # TODO(pabrik): sesuaikan dengan kapasitas disk & kebutuhan audit.
    HISTORY_RETENTION_DAYS = _int("CCTV_HISTORY_RETENTION_DAYS", 180)

    # Interval simpan snapshot produksi ke database (detik).
    HISTORY_INTERVAL = _int("CCTV_HISTORY_INTERVAL", 300)

    # ==================================================================
    # KEAMANAN
    # ==================================================================
    # TODO(pabrik): WAJIB DIISI sebelum produksi.
    # Tanpa ini, siapa pun di jaringan pabrik bisa mengirim alert palsu dan
    # mengubah status mesin.
    #
    # PERHATIAN — kunci ini TIDAK melindungi endpoint yang dipanggil browser:
    # menyimpan/menghapus kalibrasi, mengosongkan catatan deteksi, dan menutup
    # alert semuanya masih terbuka. Kunci tidak bisa dipakai di sana karena
    # harus ikut terkirim ke setiap browser yang membuka dashboard, yang sama
    # saja dengan mengumumkannya. Lihat bagian "Endpoint yang masih terbuka"
    # di PRODUCTION.md.
    # Buat token acak:  python -c "import secrets;print(secrets.token_urlsafe(32))"
    API_KEY = os.getenv("CCTV_API_KEY", "")

    # Perilaku saat API_KEY KOSONG:
    #   False (bawaan) -> endpoint tulis dibiarkan terbuka, dengan peringatan
    #                     keras di log dan di /healthz. Supaya sistem bisa
    #                     langsung dijalankan tanpa menyetel apa pun.
    #   True           -> endpoint tulis ditolak. Pakai ini bila ingin
    #                     memastikan tidak ada yang menulis tanpa kunci.
    # Begitu API_KEY diisi, kunci SELALU diperiksa — nilai ini tidak berpengaruh.
    REQUIRE_API_KEY = _bool("CCTV_REQUIRE_API_KEY", False)

    # Daftar operator yang boleh menutup alert, dipakai untuk jejak audit
    # (siapa menekan "Tindak Lanjuti").
    # TODO(pabrik): isi dari data kepegawaian, atau sambungkan ke LDAP.
    OPERATORS_FILE = os.getenv("CCTV_OPERATORS_FILE", "data/operators.json")

    # ==================================================================
    # PENGUMPULAN DATA UJI
    # ==================================================================
    # Mencatat tiap perubahan warna lampu sebagai episode (mulai-selesai),
    # untuk diunduh sebagai berkas .txt saat pengujian di lapangan.
    #   "vision" (bawaan) -> hanya dari kamera / AI worker
    #   "all"             -> termasuk data simulasi
    #   "off"             -> matikan
    EVENT_LOG = os.getenv("CCTV_EVENT_LOG", "vision").lower()

    # Episode lebih pendek dari ini diabaikan. Di tenun, lampu bisa berkedip
    # sesaat; tanpa ambang ini berkas log akan penuh kejadian tak berarti.
    EVENT_MIN_SECONDS = _int("CCTV_EVENT_MIN_SECONDS", 3)

    # ==================================================================
    # SUMBER DATA
    # ==================================================================
    # TODO(pabrik): ganti ke "live" dan isi LiveSource di app/source.py.
    # Selama masih "sim", SEMUA angka di dashboard adalah karangan.
    SOURCE = os.getenv("CCTV_SOURCE", "sim").lower()

    # Interval push data ke browser (detik).
    PUSH_INTERVAL = _int("CCTV_PUSH_INTERVAL", 5)

    # ==================================================================
    # STREAM KAMERA
    # ==================================================================
    # TODO(pabrik): arahkan ke go2rtc. {id} diganti id line ("ajl-01").
    # Nama stream di go2rtc HARUS sama dengan line_id.
    #
    #   MP4/MSE : http://192.168.1.50:1984/api/stream.mp4?src={id}
    #   WebRTC  : http://192.168.1.50:1984/stream.html?src={id}   (mode=iframe)
    #   MJPEG   : http://192.168.1.50:1984/api/frame.jpeg?src={id} (mode=img)
    STREAM_URL = os.getenv("CCTV_STREAM_URL", "")

    # TODO(pabrik): WAJIB diisi kalau menampilkan banyak kamera sekaligus.
    # Grid 18 kamera memakai main stream 1080p akan membuat PC operator
    # tersendat. Pakai sub-stream (channel 102 di Hikvision/Dahua).
    STREAM_URL_GRID = os.getenv("CCTV_STREAM_URL_GRID", "")

    STREAM_MODE = os.getenv("CCTV_STREAM_MODE", "video").lower()
    STREAM_IMG_REFRESH = _int("CCTV_STREAM_IMG_REFRESH", 2)

    # kompatibilitas lama
    STREAM_BASE = os.getenv("CCTV_STREAM_BASE", "")

    # ==================================================================
    # PEMERIKSAAN KESIAPAN
    # ==================================================================
    #: Nilai yang TERLIHAT seperti mematikan kunci, padahal justru menjadi
    #: kuncinya. "CCTV_API_KEY=false" adalah kesalahan yang paling mudah
    #: dibuat: sistem tampak terlindungi, kuncinya ketahuan siapa pun yang
    #: pernah membaca contoh berkas, dan pemeriksaan kesiapan ikut tertipu
    #: karena nilainya tidak kosong.
    WEAK_API_KEYS = {
        "false", "true", "off", "on", "no", "yes", "0", "1", "none", "null",
        "changeme", "ganti", "gantiaku", "secret", "rahasia", "password",
        "passwd", "admin", "test", "demo", "token", "apikey", "api_key",
        "isi_sama_dengan_cctv_api_key",
    }

    #: kunci acak dari token_urlsafe(32) panjangnya 43 karakter
    MIN_API_KEY_LEN = 16

    @classmethod
    def production_warnings(cls):
        """Daftar hal yang belum siap produksi. Dicetak saat startup."""
        w = []
        if cls.SOURCE != "live":
            w.append("CCTV_SOURCE masih 'sim' — semua angka produksi adalah "
                     "SIMULASI, bukan data pabrik.")
        if not cls.API_KEY:
            w.append("CCTV_API_KEY kosong — endpoint tulis tidak terlindungi. "
                     "Isi token sebelum dipakai di jaringan pabrik.")
        elif cls.API_KEY.strip().lower() in cls.WEAK_API_KEYS:
            w.append("CCTV_API_KEY diisi '%s' — ini BUKAN mematikan kunci, "
                     "melainkan menjadikannya kunci yang mudah ditebak. "
                     "Kosongkan, atau isi token acak: "
                     "python -c \"import secrets;print(secrets.token_urlsafe(32))\""
                     % cls.API_KEY)
        elif len(cls.API_KEY) < cls.MIN_API_KEY_LEN:
            w.append("CCTV_API_KEY terlalu pendek (%d karakter, minimal %d) — "
                     "bisa ditebak dengan mencoba berulang. Pakai token acak."
                     % (len(cls.API_KEY), cls.MIN_API_KEY_LEN))
        if not cls.STREAM_URL:
            w.append("CCTV_STREAM_URL kosong — kotak kamera memakai "
                     "placeholder, bukan gambar CCTV.")
        elif not cls.STREAM_URL_GRID:
            w.append("CCTV_STREAM_URL_GRID kosong — grid memakai main stream. "
                     "Pakai sub-stream supaya PC operator tidak tersendat.")
        if len(cls.SHIFT_STARTS) != len(cls.SHIFT_NAMES):
            w.append("Jumlah CCTV_SHIFT_STARTS dan CCTV_SHIFT_NAMES berbeda.")
        return w


settings = Settings()
