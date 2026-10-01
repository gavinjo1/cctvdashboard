"""Aturan alert — kapan sesuatu layak dilaporkan.

TIDAK ADA JARINGAN, TIDAK ADA KAMERA, TIDAK ADA WAKTU NYATA di berkas ini.
Masuknya angka (berapa orang, berapa mesin stop, jam berapa), keluarnya
daftar alert yang layak dikirim. Pemanggil yang mengirimkannya.

Dipisah begitu supaya aturannya bisa diuji dengan menyodorkan jam palsu —
"operator hilang 3 menit lalu kembali" bisa diperiksa dalam milidetik,
tanpa menunggu 3 menit dan tanpa dashboard yang hidup.

Tiga aturan yang ada:
  1. Operator tidak di area       tidak ada orang > absent_seconds
  2. Kerumunan di line            >= crowd_min orang > crowd_seconds
  3. Mesin stop tanpa penanganan  indikator masalah > response_seconds
                                  DAN tidak ada operator di area
"""


class Alert:
    """Satu alert yang layak dikirim. Sengaja bukan dict: bidangnya tetap."""

    __slots__ = ("label", "activity", "duration", "confidence",
                 "severity", "object_type")

    def __init__(self, label, activity, duration, confidence,
                 severity="high", object_type="Person"):
        self.label = label
        self.activity = activity
        self.duration = duration
        self.confidence = confidence
        self.severity = severity
        self.object_type = object_type

    def sebagai_argumen(self):
        """Urutan argumen untuk Pengirim.kirim_alert()."""
        return (self.label, self.activity, self.duration, self.confidence,
                self.severity, self.object_type)

    def __repr__(self):
        return "Alert(%r, %ds)" % (self.label, int(self.duration))


class AturanAlert:
    """Menyimpan penghitung waktu satu line dan menilai keadaannya.

    Satu instance per kamera, dipakai ulang antar frame — seluruh penilaian
    bergantung pada BERAPA LAMA sebuah keadaan bertahan, jadi penghitungnya
    harus hidup di antara panggilan.
    """

    def __init__(self, cfg, now):
        self.cfg = cfg
        self.last_seen_person = now
        self.crowd_since = None
        self.red_since = None

    def nilai(self, n_in_zone, n_stop, now, zone_ok=True):
        """Daftar Alert yang layak dikirim saat ini. Boleh kosong."""
        c = self.cfg

        # Tanpa zona terkalibrasi, jumlah orang SELALU nol. Menjalankan
        # aturan berbasis orang dalam keadaan itu akan membanjiri dashboard
        # dengan "Operator tidak di area" palsu dari setiap kamera yang
        # belum dikalibrasi — dan alert palsu yang banyak membuat operator
        # berhenti mempercayai semua alert.
        if not zone_ok:
            self.last_seen_person = now     # jangan menumpuk waktu absen
            self.crowd_since = None
            self.red_since = None
            return []

        if n_in_zone > 0:
            self.last_seen_person = now

        keluar = []

        # 1. operator meninggalkan line
        absent = now - self.last_seen_person
        if absent > c["absent_seconds"]:
            keluar.append(Alert(
                "Operator tidak di area",
                "Tidak ada orang terdeteksi di zona line",
                absent, 0.9))

        # 2. kerumunan
        if n_in_zone >= c["crowd_min"]:
            self.crowd_since = self.crowd_since or now
            if now - self.crowd_since > c["crowd_seconds"]:
                keluar.append(Alert(
                    "Kerumunan di line",
                    "%d orang berkumpul di zona line" % n_in_zone,
                    now - self.crowd_since, 0.85, severity="med"))
        else:
            self.crowd_since = None

        # 3. mesin stop tanpa penanganan
        if n_stop > 0:
            self.red_since = self.red_since or now
            waited = now - self.red_since
            if waited > c["response_seconds"] and n_in_zone == 0:
                keluar.append(Alert(
                    "Mesin stop tanpa penanganan",
                    "%d mesin memberi indikator masalah, "
                    "tidak ada operator di area" % n_stop,
                    waited, 0.95, object_type="Machine"))
        else:
            self.red_since = None

        return keluar


class Kehadiran:
    """Kapan operator datang dan pergi, dengan jeda terhadap deteksi putus.

    Deteksi orang putus-putus: terukur pada rekaman pabrik, celah sampai
    8,2 detik terjadi sementara operatornya tidak ke mana-mana (terhalang
    mesin, membungkuk). Tanpa jeda, tiap celah menghapus jam kedatangan dan
    waktu respons melompat maju — persis angka yang dipakai menilai operator.
    """

    def __init__(self, jeda=12.0):
        self.jeda = float(jeda)
        self.jumlah = 0            # orang pada deteksi terakhir yang sah
        self.sejak = None          # kapan kehadiran SEKARANG dimulai
        self.terlihat = 0.0        # kapan orang TERAKHIR terlihat

    def perbarui(self, n_orang, now):
        """Catat hasil satu deteksi. Return jumlah orang yang berlaku."""
        if n_orang > 0:
            self.terlihat = now
            if self.sejak is None:
                self.sejak = now              # kehadiran baru dimulai
            self.jumlah = n_orang
        elif self.sejak is not None and now - self.terlihat >= self.jeda:
            self.sejak = None
            self.jumlah = 0
        return self.jumlah
