"""Deteksi warna lampu tower mesin dari CCTV.

Tidak memakai AI — cukup analisa warna (HSV) di dalam ROI lampu.
Jauh lebih murah dan lebih akurat daripada deteksi objek, dan satu
kamera bisa membaca 10 lampu sekaligus tanpa tambahan beban berarti.

Pemetaan warna -> status mesin (standar umum tower light pabrik):
    hijau  -> run    (mesin jalan)
    kuning -> idle   (setting / peringatan)
    merah  -> stop   (berhenti / alarm)
    padam  -> off    (mesin mati / tidak ada daya)
"""
import cv2
import numpy as np

# rentang HSV per warna. Merah melintasi batas hue 0/180, jadi dua rentang.
RANGES = {
    "red":    [((0, 110, 120), (10, 255, 255)),
               ((170, 110, 120), (180, 255, 255))],
    "yellow": [((18, 110, 130), (35, 255, 255))],
    "green":  [((40, 80, 100), (85, 255, 255))],
}

COLOR_TO_STATUS = {"red": "stop", "yellow": "idle", "green": "run", "off": "off"}

# warna gambar overlay (BGR)
DRAW = {"red": (60, 60, 240), "yellow": (60, 200, 240),
        "green": (80, 220, 90), "off": (130, 130, 130)}


def rect_from_percent(rect, w, h):
    """[x, y, w, h] dalam persen -> piksel."""
    x, y, rw, rh = rect
    return (int(x / 100 * w), int(y / 100 * h),
            max(1, int(rw / 100 * w)), max(1, int(rh / 100 * h)))


def detect_lamp(frame, roi_px, min_ratio=0.10):
    """Baca warna lampu di dalam ROI.

    roi_px   : (x, y, w, h) dalam piksel
    min_ratio: proporsi minimum piksel berwarna agar dianggap menyala

    Return: (warna, rasio) — warna salah satu dari red/yellow/green/off
    """
    x, y, w, h = roi_px
    crop = frame[max(0, y):y + h, max(0, x):x + w]
    if crop.size == 0:
        return "off", 0.0

    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    total = crop.shape[0] * crop.shape[1]

    best, best_ratio = "off", 0.0
    for color, ranges in RANGES.items():
        mask = None
        for lo, hi in ranges:
            m = cv2.inRange(hsv, np.array(lo), np.array(hi))
            mask = m if mask is None else (mask | m)
        ratio = float(np.count_nonzero(mask)) / total
        if ratio > best_ratio:
            best, best_ratio = color, ratio

    if best_ratio < min_ratio:
        return "off", best_ratio
    return best, best_ratio


def read_all(frame, lamps, min_ratio=0.10):
    """Baca semua lampu dalam satu frame.

    lamps: [{"no": 1, "lamp": [x, y, w, h]}, ...]  (persen)
    Return: (hasil_per_mesin, ringkasan_jumlah)
    """
    h, w = frame.shape[:2]
    out, counts = [], {"run": 0, "idle": 0, "stop": 0, "off": 0}
    for m in lamps:
        roi = rect_from_percent(m["lamp"], w, h)
        color, ratio = detect_lamp(frame, roi, min_ratio)
        status = COLOR_TO_STATUS[color]
        counts[status] += 1
        out.append({"no": m["no"], "color": color, "status": status,
                    "ratio": round(ratio, 3), "roi": roi})
    return out, counts


def draw_lamps(frame, results, show_label=True):
    """Gambar kotak ROI + warna terbaca ke frame (untuk verifikasi visual)."""
    for r in results:
        x, y, w, h = r["roi"]
        col = DRAW[r["color"]]
        cv2.rectangle(frame, (x, y), (x + w, y + h), col, 2)
        if show_label:
            cv2.putText(frame, "%02d %s" % (r["no"], r["color"]),
                        (x, max(12, y - 5)), cv2.FONT_HERSHEY_SIMPLEX,
                        0.42, col, 1, cv2.LINE_AA)
    return frame
