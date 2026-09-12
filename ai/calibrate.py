#!/usr/bin/env python3
"""Alat kalibrasi visual — tentukan zona line & ROI lampu dengan mouse.

Buka gambar kamera di browser, gambar zonanya, gambar kotak lampunya,
lalu salin JSON yang dihasilkan ke ai/zones.json.

    python ai/calibrate.py --source rtsp://192.168.1.50:8554/ajl-01 --line-id ajl-01
    python ai/calibrate.py --device 1 --line-id ajl-01          # webcam
    python ai/calibrate.py --source foto.jpg --line-id ajl-01   # dari foto

Lalu buka http://127.0.0.1:1985

Warna lampu dibaca ulang tiap detik memakai ROI yang sedang digambar,
jadi ketepatan kalibrasi langsung kelihatan tanpa menjalankan worker.
"""
import argparse
import json
import logging
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lamp import buat_pembaca, read_tower

#: urut ATAS -> BAWAH. TODO(pabrik): samakan dengan tabel bagian perawatan.
INDIKATOR = ["mesin_stop", "putus_pakan", "putus_lusi", "setup"]

log = logging.getLogger("calibrate")

state = {"jpeg": None, "lamps": [], "lock": threading.Lock(), "size": (0, 0)}


class Grabber(threading.Thread):
    """Ambil frame terus-menerus supaya gambar di browser tetap segar."""

    def __init__(self, cap, is_image, max_width):
        super().__init__(daemon=True)
        self.cap, self.is_image, self.max_width = cap, is_image, max_width
        self.stop_flag = threading.Event()

    def run(self):
        while not self.stop_flag.is_set():
            ok, frame = self.cap.read()
            if not ok:
                if self.is_image:
                    self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                time.sleep(0.3)
                continue

            if self.max_width and frame.shape[1] > self.max_width:
                sc = self.max_width / frame.shape[1]
                frame = cv2.resize(frame, (self.max_width,
                                           int(frame.shape[0] * sc)))

            with state["lock"]:
                lamps = list(state["lamps"])
            # Pembaca dibangun ulang hanya kalau kotak berubah — riwayat
            # kedip tersimpan di dalamnya dan harus bertahan antar frame.
            if lamps != self._lamps_terakhir:
                self._lamps_terakhir = list(lamps)
                self._pembaca = buat_pembaca(lamps, INDIKATOR)
                for pb in self._pembaca:
                    pb.rekam_baseline(frame)
            results = read_tower(frame, self._pembaca)[0] if self._pembaca else []

            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                with state["lock"]:
                    state["jpeg"] = buf.tobytes()
                    state["size"] = (frame.shape[1], frame.shape[0])
                    state["reading"] = [
                        {"no": r["no"], "status": r["status"],
                         "indikator": r["indikator"], "kedip": r["kedip"],
                         "segmen": [{"indikator": b["indikator"],
                                     "keadaan": b["keadaan"],
                                     "v90": b["v90"], "margin": b["margin"]}
                                    for b in r["segmen"]]}
                        for r in results]
            time.sleep(0.2)


PAGE = r"""<!doctype html><html lang="id"><head><meta charset="utf-8">
<title>Kalibrasi Kamera</title><style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#e6edf3;font:13px/1.5 system-ui,sans-serif;padding:16px}
h1{font-size:16px;margin-bottom:4px}
.sub{color:#8b949e;font-size:12px;margin-bottom:14px}
.wrap{display:grid;grid-template-columns:1fr 380px;gap:16px;align-items:start}
.stage{position:relative;background:#000;border:1px solid #262d3a;border-radius:10px;
  overflow:hidden;line-height:0}
#img{width:100%;display:block}
#cv{position:absolute;inset:0;width:100%;height:100%;cursor:crosshair}
.panel{background:#161b22;border:1px solid #262d3a;border-radius:10px;padding:14px}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:#8b949e;
  margin:14px 0 8px}
h2:first-child{margin-top:0}
button{background:#1c2230;border:1px solid #262d3a;color:#e6edf3;padding:7px 12px;
  border-radius:7px;cursor:pointer;font-size:12px;font-family:inherit;margin:0 4px 6px 0}
button:hover{border-color:#2f81f7}
button.on{background:#2f81f7;border-color:#2f81f7;color:#fff}
button.danger{border-color:rgba(248,81,73,.5);color:#f85149}
.hint{color:#8b949e;font-size:11.5px;line-height:1.7;margin:8px 0}
.hint b{color:#e6edf3}
textarea{width:100%;height:230px;background:#0d1117;border:1px solid #262d3a;
  color:#e6edf3;border-radius:7px;padding:10px;font:11px/1.5 ui-monospace,monospace;
  resize:vertical}
.row{display:flex;align-items:center;gap:8px;margin-bottom:8px}
input[type=number]{background:#0d1117;border:1px solid #262d3a;color:#e6edf3;
  border-radius:6px;padding:6px 8px;width:70px;font-family:inherit}
.reads{display:flex;flex-wrap:wrap;gap:5px;margin-top:8px}
.rd{font-size:10.5px;padding:3px 8px;border-radius:5px;border:1px solid #262d3a}
.rd.green{color:#56d364;border-color:rgba(63,185,80,.5)}
.rd.red{color:#f85149;border-color:rgba(248,81,73,.5)}
.rd.yellow{color:#e3b341;border-color:rgba(210,153,34,.5)}
.rd.off{color:#6e7681}
</style></head><body>

<h1>Kalibrasi Kamera — <span id="lineId"></span></h1>
<div class="sub">Gambar zona line dan kotak lampu tower, lalu salin JSON ke <code>ai/zones.json</code></div>

<div class="wrap">
  <div class="stage">
    <img id="img" alt="frame kamera">
    <canvas id="cv"></canvas>
  </div>

  <div class="panel">
    <h2>Mode</h2>
    <button id="mZone" class="on">Zona Line</button>
    <button id="mLamp">Kotak Lampu</button>

    <div id="hintZone" class="hint">
      <b>Klik</b> untuk menambah titik sudut zona.<br>
      Zona = area line yang dipantau. Orang dihitung kalau <b>titik kakinya</b>
      berada di dalam zona ini.
    </div>
    <div id="hintLamp" class="hint" style="display:none">
      <b>Seret</b> untuk menggambar kotak di sekeliling satu lampu tower.<br>
      <b>Klik kotak</b> untuk menghapusnya.<br>
      Untuk 10 mesin berderet: gambar <b>satu kotak panjang</b> menutupi semua
      lampu, lalu tekan Bagi Rata.
    </div>

    <div class="row" id="splitRow" style="display:none">
      <button id="split">Bagi Rata</button>
      <input type="number" id="splitN" value="10" min="2" max="30">
      <span class="hint" style="margin:0">kotak</span>
    </div>

    <button id="undo">Batal Terakhir</button>
    <button id="reset" class="danger">Hapus Semua</button>

    <h2>Pembacaan Lampu (langsung)</h2>
    <div class="reads" id="reads"><span class="hint">belum ada kotak lampu</span></div>

    <h2>Hasil JSON</h2>
    <button id="copy">Salin</button>
    <button id="save">Simpan ke zones.generated.json</button>
    <textarea id="out" readonly></textarea>
  </div>
</div>

<script>
const img = document.getElementById('img'), cv = document.getElementById('cv');
const ctx = cv.getContext('2d');
let mode = 'zone', zone = [], lamps = [], drag = null;
const LINE_ID = new URLSearchParams(location.search).get('line') || window.LINE_ID || 'ajl-01';
document.getElementById('lineId').textContent = LINE_ID;

/* ---- gambar ulang frame tiap 500ms ---- */
setInterval(() => { img.src = '/frame.jpg?t=' + Date.now(); }, 500);
img.onload = () => { cv.width = img.clientWidth; cv.height = img.clientHeight; draw(); };

/* ---- konversi piksel layar <-> persen ---- */
const toPct = (x, y) => [x / cv.width * 100, y / cv.height * 100];
const toPx  = (x, y) => [x / 100 * cv.width, y / 100 * cv.height];

function draw() {
  ctx.clearRect(0, 0, cv.width, cv.height);

  // zona
  if (zone.length) {
    ctx.beginPath();
    zone.forEach(([x, y], i) => {
      const [px, py] = toPx(x, y);
      i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
    });
    if (zone.length > 2) ctx.closePath();
    ctx.fillStyle = 'rgba(80,200,80,.18)';
    ctx.strokeStyle = '#50c850'; ctx.lineWidth = 2;
    if (zone.length > 2) ctx.fill();
    ctx.stroke();
    zone.forEach(([x, y]) => {
      const [px, py] = toPx(x, y);
      ctx.fillStyle = '#50c850';
      ctx.beginPath(); ctx.arc(px, py, 4, 0, 7); ctx.fill();
    });
  }

  // kotak lampu
  lamps.forEach((l, i) => {
    const [px, py] = toPx(l[0], l[1]), [pw, ph] = toPx(l[2], l[3]);
    ctx.strokeStyle = '#2f81f7'; ctx.lineWidth = 2;
    ctx.strokeRect(px, py, pw, ph);
    ctx.fillStyle = '#2f81f7'; ctx.font = '11px system-ui';
    ctx.fillText(String(i + 1).padStart(2, '0'), px + 2, py - 4);
  });

  // kotak yang sedang diseret
  if (drag) {
    ctx.setLineDash([5, 4]); ctx.strokeStyle = '#58a6ff';
    ctx.strokeRect(drag.x, drag.y, drag.w, drag.h);
    ctx.setLineDash([]);
  }
  output();
}

/* ---- mouse ---- */
function pos(e) {
  const r = cv.getBoundingClientRect();
  return [e.clientX - r.left, e.clientY - r.top];
}
cv.onmousedown = e => {
  const [x, y] = pos(e);
  if (mode === 'zone') { zone.push(toPct(x, y)); draw(); return; }
  // klik di dalam kotak = hapus kotak itu
  const hit = lamps.findIndex(l => {
    const [px, py] = toPx(l[0], l[1]), [pw, ph] = toPx(l[2], l[3]);
    return x >= px && x <= px + pw && y >= py && y <= py + ph;
  });
  if (hit >= 0) { lamps.splice(hit, 1); draw(); return; }
  drag = { x, y, w: 0, h: 0 };
};
cv.onmousemove = e => {
  if (!drag) return;
  const [x, y] = pos(e);
  drag.w = x - drag.x; drag.h = y - drag.y;
  draw();
};
cv.onmouseup = () => {
  if (!drag) return;
  let { x, y, w, h } = drag;
  if (w < 0) { x += w; w = -w; }
  if (h < 0) { y += h; h = -h; }
  if (w > 6 && h > 6) {
    const [px, py] = toPct(x, y), [pw, ph] = toPct(w, h);
    lamps.push([r2(px), r2(py), r2(pw), r2(ph)]);
  }
  drag = null; draw(); push();
};
const r2 = v => Math.round(v * 100) / 100;

/* ---- tombol ---- */
function setMode(m) {
  mode = m;
  document.getElementById('mZone').classList.toggle('on', m === 'zone');
  document.getElementById('mLamp').classList.toggle('on', m === 'lamp');
  document.getElementById('hintZone').style.display = m === 'zone' ? '' : 'none';
  document.getElementById('hintLamp').style.display = m === 'lamp' ? '' : 'none';
  document.getElementById('splitRow').style.display = m === 'lamp' ? 'flex' : 'none';
  cv.style.cursor = m === 'zone' ? 'crosshair' : 'cell';
}
document.getElementById('mZone').onclick = () => setMode('zone');
document.getElementById('mLamp').onclick = () => setMode('lamp');

document.getElementById('undo').onclick = () => {
  mode === 'zone' ? zone.pop() : lamps.pop();
  draw(); push();
};
document.getElementById('reset').onclick = () => {
  if (mode === 'zone') zone = []; else lamps = [];
  draw(); push();
};

// satu kotak panjang -> N kotak sama lebar
document.getElementById('split').onclick = () => {
  if (lamps.length !== 1) {
    alert('Gambar SATU kotak panjang yang menutupi semua lampu, lalu tekan Bagi Rata.');
    return;
  }
  const n = Math.max(2, +document.getElementById('splitN').value);
  const [x, y, w, h] = lamps[0];
  const cw = w / n;
  lamps = [];
  for (let i = 0; i < n; i++) {
    lamps.push([r2(x + i * cw + cw * 0.12), r2(y), r2(cw * 0.76), r2(h)]);
  }
  draw(); push();
};

/* ---- keluaran ---- */
function config() {
  const c = { line_id: LINE_ID, rtsp: "ISI_URL_RTSP_KAMERA_DI_SINI" };
  if (zone.length > 2) c.zone = zone.map(p => [r2(p[0]), r2(p[1])]);
  if (lamps.length) c.machines = lamps.map((l, i) => ({ no: i + 1, lamp: l }));
  return c;
}
function output() {
  document.getElementById('out').value = JSON.stringify(config(), null, 2);
}
document.getElementById('copy').onclick = () => {
  navigator.clipboard.writeText(document.getElementById('out').value);
  const b = document.getElementById('copy');
  b.textContent = 'Tersalin'; setTimeout(() => b.textContent = 'Salin', 1200);
};
document.getElementById('save').onclick = async () => {
  const r = await fetch('/save', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config()) });
  const j = await r.json();
  alert(j.ok ? 'Disimpan ke ' + j.path : 'Gagal: ' + j.error);
};

/* ---- kirim ROI ke server supaya warnanya dibaca ---- */
async function push() {
  await fetch('/lamps', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(config().machines || []) });
}
setInterval(async () => {
  const r = await fetch('/reading').then(x => x.json()).catch(() => null);
  const box = document.getElementById('reads');
  if (!r || !r.length) {
    box.innerHTML = '<span class="hint">belum ada kotak lampu</span>';
    return;
  }
  box.innerHTML = r.map(x =>
    `<span class="rd ${x.color}">${String(x.no).padStart(2,'0')} ${x.color} ${(x.ratio*100).toFixed(0)}%</span>`
  ).join('');
}, 1000);

setMode('zone');
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    line_id = "ajl-01"
    out_path = Path("ai/zones.generated.json")

    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            page = PAGE.replace("window.LINE_ID || 'ajl-01'",
                                "'%s'" % self.line_id)
            self._send(200, "text/html; charset=utf-8", page.encode())
        elif path == "/frame.jpg":
            with state["lock"]:
                data = state["jpeg"]
            if data is None:
                self.send_error(503, "belum ada frame")
            else:
                self._send(200, "image/jpeg", data)
        elif path == "/reading":
            with state["lock"]:
                body = json.dumps(state.get("reading", [])).encode()
            self._send(200, "application/json", body)
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) if n else b"[]"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self._send(400, "application/json", b'{"error":"json tidak valid"}')
            return

        if path == "/lamps":
            with state["lock"]:
                state["lamps"] = data or []
            self._send(200, "application/json", b'{"ok":true}')
        elif path == "/save":
            try:
                self.out_path.parent.mkdir(parents=True, exist_ok=True)
                self.out_path.write_text(json.dumps(data, indent=2,
                                                    ensure_ascii=False))
                body = json.dumps({"ok": True,
                                   "path": str(self.out_path)}).encode()
                log.info("Konfigurasi disimpan ke %s", self.out_path)
            except OSError as e:
                body = json.dumps({"ok": False, "error": str(e)}).encode()
            self._send(200, "application/json", body)
        else:
            self.send_error(404)


def main():
    p = argparse.ArgumentParser(description="Kalibrasi zona & ROI lampu")
    p.add_argument("--source", default="",
                   help="URL RTSP/HTTP, path file video, atau file gambar")
    p.add_argument("--device", type=int, default=1, help="index webcam")
    p.add_argument("--line-id", default="ajl-01")
    p.add_argument("--port", type=int, default=1985)
    p.add_argument("--max-width", type=int, default=1280)
    p.add_argument("--out", default="ai/zones.generated.json")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-7s %(message)s")

    is_image = False
    if args.source:
        log.info("Membuka sumber: %s", args.source)
        cap = cv2.VideoCapture(args.source, cv2.CAP_FFMPEG)
        is_image = Path(args.source).suffix.lower() in (
            ".jpg", ".jpeg", ".png", ".bmp")
    else:
        log.info("Membuka webcam (device %d) ...", args.device)
        cap = cv2.VideoCapture(args.device)

    if not cap.isOpened():
        raise SystemExit("Sumber tidak bisa dibuka. Untuk webcam, coba "
                         "--device 0 / --device 2, dan pastikan izin kamera "
                         "sudah diberikan di System Settings.")

    Handler.line_id = args.line_id
    Handler.out_path = Path(args.out)

    g = Grabber(cap, is_image, args.max_width)
    g.start()

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    log.info("")
    log.info("  Buka di browser:  http://127.0.0.1:%d", args.port)
    log.info("")
    log.info("  1. Mode 'Zona Line'   -> klik sudut-sudut area line")
    log.info("  2. Mode 'Kotak Lampu' -> seret satu kotak panjang menutupi")
    log.info("     semua lampu, lalu tekan 'Bagi Rata' dengan jumlah mesin")
    log.info("  3. Cek panel 'Pembacaan Lampu' — warnanya harus sesuai")
    log.info("  4. Salin JSON ke ai/zones.json")
    log.info("")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        g.stop_flag.set()
        srv.shutdown()


if __name__ == "__main__":
    main()
