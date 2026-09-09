/* ================================================================
   Kalibrasi kamera langsung di dashboard.

   Gambar zona line & ROI lampu tower di atas feed kamera, simpan ke
   server, lalu AI worker mengambilnya lewat API — tidak perlu menyalin
   file ke server AI.

   Pembacaan warna lampu dilakukan di browser (canvas + HSV) supaya
   hasil kalibrasi langsung terlihat. Ambang batasnya SENGAJA dibuat
   sama dengan ai/lamp.py; kalau salah satu diubah, ubah keduanya.
   ================================================================ */

const CAL = {
  on: false,
  mode: "zone",           // zone | lamp
  zone: [],               // [[x%, y%], ...]
  lamps: [],              // [[x%, y%, w%, h%], ...]
  drag: null,
  lineId: null,
};

const r2 = v => Math.round(v * 100) / 100;

/* ---------- HSV: harus sama dengan ai/lamp.py ---------- */
const HSV_RANGES = {
  red:    [[[0, 110, 120], [10, 255, 255]], [[170, 110, 120], [180, 255, 255]]],
  yellow: [[[18, 110, 130], [35, 255, 255]]],
  green:  [[[40, 80, 100], [85, 255, 255]]],
};
const MIN_RATIO = 0.10;

/* konversi RGB -> HSV dengan skala OpenCV (H 0-180, S/V 0-255) */
function rgb2hsv(r, g, b) {
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
  let h = 0;
  if (d) {
    if (mx === r) h = 60 * (((g - b) / d) % 6);
    else if (mx === g) h = 60 * ((b - r) / d + 2);
    else h = 60 * ((r - g) / d + 4);
  }
  if (h < 0) h += 360;
  return [h / 2, mx ? (d / mx) * 255 : 0, mx];      // H dibagi 2 = skala OpenCV
}

function inRange(hsv, [lo, hi]) {
  return hsv[0] >= lo[0] && hsv[0] <= hi[0]
      && hsv[1] >= lo[1] && hsv[1] <= hi[1]
      && hsv[2] >= lo[2] && hsv[2] <= hi[2];
}

/* baca warna dominan di dalam satu ROI pada gambar kamera */
function readLamp(imgEl, roiPct) {
  const w = imgEl.naturalWidth, h = imgEl.naturalHeight;
  if (!w || !h) return { color: "off", ratio: 0 };

  const [px, py, pw, ph] = [
    Math.round(roiPct[0] / 100 * w), Math.round(roiPct[1] / 100 * h),
    Math.max(1, Math.round(roiPct[2] / 100 * w)),
    Math.max(1, Math.round(roiPct[3] / 100 * h)),
  ];

  const c = readLamp._cv || (readLamp._cv = document.createElement("canvas"));
  c.width = pw; c.height = ph;
  const cx = c.getContext("2d", { willReadFrequently: true });

  // "blocked" != "off". Kalau canvas ter-taint kita TIDAK tahu warnanya;
  // menampilkannya sebagai "padam" akan menyesatkan saat kalibrasi.
  let data;
  try {
    cx.drawImage(imgEl, px, py, pw, ph, 0, 0, pw, ph);
    data = cx.getImageData(0, 0, pw, ph).data;
  } catch (e) {
    return { color: "blocked", ratio: 0 };
  }

  const total = pw * ph;
  const hit = { red: 0, yellow: 0, green: 0 };
  const step = total > 4000 ? 4 : 1;          // subsample kalau ROI besar
  let counted = 0;

  for (let i = 0; i < total; i += step) {
    const o = i * 4;
    const hsv = rgb2hsv(data[o], data[o + 1], data[o + 2]);
    counted++;
    for (const color in HSV_RANGES) {
      if (HSV_RANGES[color].some(rg => inRange(hsv, rg))) { hit[color]++; break; }
    }
  }

  let best = "off", bestRatio = 0;
  for (const color in hit) {
    const ratio = hit[color] / counted;
    if (ratio > bestRatio) { best = color; bestRatio = ratio; }
  }
  return bestRatio < MIN_RATIO ? { color: "off", ratio: bestRatio }
                               : { color: best, ratio: bestRatio };
}

const COLOR_STATUS = { green: "run", yellow: "idle", red: "stop", off: "off" };
const COLOR_HEX = { green: "#3fb950", yellow: "#d29922", red: "#f85149",
                    off: "#6e7681", blocked: "#8b949e" };

/* ---------- menggambar ---------- */
function calCanvas() { return document.getElementById("calCanvas"); }
function calImg() { return document.querySelector("#detailCam2 img, #detailCam2 video"); }

function calResize() {
  const c = calCanvas(), box = document.getElementById("detailCam2");
  if (!c || !box) return;
  const r = box.getBoundingClientRect();
  if (c.width !== Math.round(r.width) || c.height !== Math.round(r.height)) {
    c.width = Math.round(r.width);
    c.height = Math.round(r.height);
  }
}

/* Area gambar yang benar-benar tampil di dalam kotak.
   Dengan object-fit:contain gambar di-letterbox kalau rasionya beda dari
   kotaknya. Persen HARUS dihitung terhadap area ini, bukan terhadap kanvas,
   kalau tidak koordinat akan melenceng. */
function calContentRect() {
  const c = calCanvas();
  const img = calImg();
  const W = c.width, H = c.height;
  const nw = img && (img.naturalWidth || img.videoWidth);
  const nh = img && (img.naturalHeight || img.videoHeight);
  if (!nw || !nh) return { x: 0, y: 0, w: W, h: H };

  const scale = Math.min(W / nw, H / nh);          // contain
  const w = nw * scale, h = nh * scale;
  return { x: (W - w) / 2, y: (H - h) / 2, w, h };
}

function calDraw() {
  const c = calCanvas();
  if (!c || c.hidden) return;
  calResize();
  const ctx = c.getContext("2d");
  const R = calContentRect();
  const toPx = (x, y) => [R.x + x / 100 * R.w, R.y + y / 100 * R.h];
  const toLen = (w, h) => [w / 100 * R.w, h / 100 * R.h];
  ctx.clearRect(0, 0, c.width, c.height);

  // tandai area di luar gambar supaya jelas tidak bisa digambari
  if (R.x > 0.5 || R.y > 0.5) {
    ctx.fillStyle = "rgba(0,0,0,.55)";
    if (R.x > 0.5) {
      ctx.fillRect(0, 0, R.x, c.height);
      ctx.fillRect(R.x + R.w, 0, c.width - R.x - R.w, c.height);
    }
    if (R.y > 0.5) {
      ctx.fillRect(0, 0, c.width, R.y);
      ctx.fillRect(0, R.y + R.h, c.width, c.height - R.y - R.h);
    }
  }

  // zona
  if (CAL.zone.length) {
    ctx.beginPath();
    CAL.zone.forEach(([x, y], i) => {
      const [px, py] = toPx(x, y);
      i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
    });
    if (CAL.zone.length > 2) { ctx.closePath(); ctx.fillStyle = "rgba(63,185,80,.18)"; ctx.fill(); }
    ctx.strokeStyle = "#3fb950"; ctx.lineWidth = 2; ctx.stroke();
    CAL.zone.forEach(([x, y]) => {
      const [px, py] = toPx(x, y);
      ctx.fillStyle = "#3fb950";
      ctx.beginPath(); ctx.arc(px, py, 4, 0, 7); ctx.fill();
    });
  }

  // ROI lampu — diberi warna sesuai hasil baca
  const img = calImg();
  const reads = [];
  CAL.lamps.forEach((l, i) => {
    const [px, py] = toPx(l[0], l[1]), [pw, ph] = toLen(l[2], l[3]);
    let col = "#2f81f7";
    if (img && img.tagName === "IMG") {
      const rd = readLamp(img, l);
      reads.push({ no: i + 1, ...rd });
      col = COLOR_HEX[rd.color];
    }
    ctx.strokeStyle = col; ctx.lineWidth = 2;
    ctx.strokeRect(px, py, pw, ph);
    ctx.fillStyle = col;
    ctx.font = "600 11px system-ui";
    ctx.fillText(String(i + 1).padStart(2, "0"), px + 2, Math.max(11, py - 4));
  });

  if (CAL.drag) {
    ctx.setLineDash([5, 4]); ctx.strokeStyle = "#58a6ff"; ctx.lineWidth = 2;
    ctx.strokeRect(CAL.drag.x, CAL.drag.y, CAL.drag.w, CAL.drag.h);
    ctx.setLineDash([]);
  }

  calRenderReads(reads);
}

function calRenderReads(reads) {
  const box = document.getElementById("calReads");
  if (!box) return;
  if (!CAL.lamps.length) {
    box.innerHTML = `<span class="calbar-hint">Belum ada kotak lampu.</span>`;
    return;
  }
  if (!reads.length) {
    box.innerHTML = `<span class="calbar-hint">Pembacaan warna hanya jalan di mode stream gambar (CCTV_STREAM_MODE=img).</span>`;
    return;
  }
  if (reads.every(r => r.color === "blocked")) {
    box.innerHTML = `<span class="calbar-hint cal-blocked">
      Warna tidak bisa dibaca dari browser — sumber stream tidak mengirim
      header <b>Access-Control-Allow-Origin</b>. Kotaknya tetap tersimpan
      dan tetap dibaca AI worker; hanya pratinjau warna di sini yang mati.
      </span>`;
    return;
  }
  const n = { run: 0, idle: 0, stop: 0, off: 0 };
  reads.forEach(r => { const st = COLOR_STATUS[r.color]; if (st) n[st]++; });
  box.innerHTML =
    `<span class="cal-sum">${n.run} jalan · ${n.idle} setting · ${n.stop} stop · ${n.off} mati</span>` +
    reads.map(r => `<span class="cal-rd ${r.color}">${String(r.no).padStart(2, "0")}
      ${r.color === "blocked" ? "?" : r.color} ${r.color === "blocked" ? "" : (r.ratio * 100).toFixed(0) + "%"}</span>`).join("");
}

/* ---------- interaksi mouse ---------- */
function calBind() {
  const c = calCanvas();
  if (!c || c.dataset.bound) return;
  c.dataset.bound = "1";

  const pos = e => {
    const r = c.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  };
  const toPct = (x, y) => {
    const R = calContentRect();
    return [r2((x - R.x) / R.w * 100), r2((y - R.y) / R.h * 100)];
  };
  const toPctLen = (w, h) => {
    const R = calContentRect();
    return [r2(w / R.w * 100), r2(h / R.h * 100)];
  };
  const inContent = (x, y) => {
    const R = calContentRect();
    return x >= R.x && x <= R.x + R.w && y >= R.y && y <= R.y + R.h;
  };

  c.addEventListener("mousedown", e => {
    e.stopPropagation();
    const [x, y] = pos(e);
    if (!inContent(x, y)) return;          // klik di area letterbox: abaikan
    if (CAL.mode === "zone") { CAL.zone.push(toPct(x, y)); calDraw(); return; }
    const R = calContentRect();
    const hit = CAL.lamps.findIndex(l => {
      const px = R.x + l[0] / 100 * R.w, py = R.y + l[1] / 100 * R.h;
      const pw = l[2] / 100 * R.w, ph = l[3] / 100 * R.h;
      return x >= px && x <= px + pw && y >= py && y <= py + ph;
    });
    if (hit >= 0) { CAL.lamps.splice(hit, 1); calDraw(); return; }
    CAL.drag = { x, y, w: 0, h: 0 };
  });

  c.addEventListener("mousemove", e => {
    if (!CAL.drag) return;
    const [x, y] = pos(e);
    CAL.drag.w = x - CAL.drag.x; CAL.drag.h = y - CAL.drag.y;
    calDraw();
  });

  c.addEventListener("mouseup", e => {
    e.stopPropagation();
    if (!CAL.drag) return;
    let { x, y, w, h } = CAL.drag;
    if (w < 0) { x += w; w = -w; }
    if (h < 0) { y += h; h = -h; }
    if (w > 6 && h > 6) {
      CAL.lamps.push([...toPct(x, y), ...toPctLen(w, h)]);
    }
    CAL.drag = null; calDraw();
  });

  c.addEventListener("click", e => e.stopPropagation());
}

/* ---------- kontrol ---------- */
function calSetMode(m) {
  CAL.mode = m;
  document.getElementById("calZone").classList.toggle("on", m === "zone");
  document.getElementById("calLamp").classList.toggle("on", m === "lamp");
  ["calSplitLbl", "calSplitN", "calSplit"].forEach(id =>
    document.getElementById(id).hidden = (m !== "lamp"));
  document.getElementById("calHint").innerHTML = m === "zone"
    ? "<b>Klik</b> di gambar untuk menambah titik sudut zona. Orang dihitung kalau <b>titik kakinya</b> ada di dalam zona."
    : "<b>Seret</b> untuk menggambar kotak di sekeliling lampu. <b>Klik kotak</b> untuk menghapus. Untuk 10 mesin: gambar <b>satu kotak panjang</b> menutupi semua lampu lalu tekan Bagi Rata.";
  const c = calCanvas();
  if (c) c.style.cursor = m === "zone" ? "crosshair" : "cell";
  calDraw();
}

function calOpen(line) {
  CAL.on = true;
  CAL.lineId = line.id;
  const cal = line.cal || { zone: [], machines: [] };
  CAL.zone = (cal.zone || []).map(p => [p[0], p[1]]);
  CAL.lamps = (cal.machines || []).map(m => m.lamp.slice());
  document.getElementById("calBar").hidden = false;
  const c = calCanvas();
  c.hidden = false;
  calBind();
  calSetMode("zone");
  document.getElementById("calToggle").classList.add("on");
}

function calClose() {
  CAL.on = false;
  CAL.drag = null;
  document.getElementById("calBar").hidden = true;
  const c = calCanvas();
  if (c) { c.hidden = true; c.getContext("2d").clearRect(0, 0, c.width, c.height); }
  document.getElementById("calToggle").classList.remove("on");
}

function calMsg(text, ok) {
  const el = document.getElementById("calMsg");
  el.textContent = text;
  el.className = "cal-msg " + (ok ? "ok" : "bad");
  setTimeout(() => { el.textContent = ""; }, 3000);
}

async function calSave() {
  if (CAL.zone.length && CAL.zone.length < 3) {
    calMsg("Zona butuh minimal 3 titik", false);
    return;
  }
  const body = {
    zone: CAL.zone,
    machines: CAL.lamps.map((l, i) => ({ no: i + 1, lamp: l })),
  };
  try {
    const r = await fetch(`/api/lines/${CAL.lineId}/calibration`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      calMsg(e.detail || `Gagal (${r.status})`, false);
      return;
    }
    calMsg(`Tersimpan — ${body.zone.length} titik zona, ${body.machines.length} lampu`, true);
  } catch (e) {
    calMsg("Gagal menghubungi server", false);
  }
}

function calBindControls() {
  document.getElementById("calToggle").onclick = () => {
    if (CAL.on) { calClose(); return; }
    const l = byId(detailLine);
    if (l) calOpen(l);
  };
  document.getElementById("calZone").onclick = () => calSetMode("zone");
  document.getElementById("calLamp").onclick = () => calSetMode("lamp");
  document.getElementById("calUndo").onclick = () => {
    CAL.mode === "zone" ? CAL.zone.pop() : CAL.lamps.pop();
    calDraw();
  };
  // "Bersihkan Gambar" hanya mengosongkan kanvas — yang tersimpan di server
  // TIDAK tersentuh sampai Simpan ditekan.
  document.getElementById("calClear").onclick = () => {
    if (CAL.mode === "zone") CAL.zone = []; else CAL.lamps = [];
    calDraw();
    calMsg("Kanvas dibersihkan — tekan Simpan untuk menerapkannya", true);
  };

  // "Hapus Kalibrasi" menghapus data tersimpan untuk KAMERA INI SAJA.
  document.getElementById("calDelete").onclick = async () => {
    const line = byId(CAL.lineId);
    const nama = line ? line.name : CAL.lineId;
    if (!confirm(`Hapus kalibrasi tersimpan untuk ${nama}?\n\n` +
                 `Kamera lain tidak terpengaruh. AI worker akan kembali ` +
                 `memakai zones.json untuk kamera ini.`)) return;
    try {
      const r = await fetch(`/api/lines/${CAL.lineId}/calibration`,
                            { method: "DELETE" });
      if (r.status === 404) { calMsg("Kamera ini memang belum dikalibrasi", false); return; }
      if (!r.ok) { calMsg(`Gagal (${r.status})`, false); return; }
      CAL.zone = []; CAL.lamps = [];
      calDraw();
      calMsg(`Kalibrasi ${nama} dihapus`, true);
    } catch (e) {
      calMsg("Gagal menghubungi server", false);
    }
  };
  document.getElementById("calSplit").onclick = () => {
    if (CAL.lamps.length !== 1) {
      calMsg("Gambar SATU kotak panjang dulu, lalu Bagi Rata", false);
      return;
    }
    const n = Math.max(2, +document.getElementById("calSplitN").value);
    const [x, y, w, h] = CAL.lamps[0];
    const cw = w / n;
    CAL.lamps = [];
    for (let i = 0; i < n; i++) {
      CAL.lamps.push([r2(x + i * cw + cw * 0.12), r2(y), r2(cw * 0.76), r2(h)]);
    }
    calDraw();
  };
  document.getElementById("calSave").onclick = calSave;
}

/* gambar ulang mengikuti frame kamera yang menyegar tiap detik */
setInterval(() => { if (CAL.on) calDraw(); }, 1000);
window.addEventListener("resize", () => { if (CAL.on) calDraw(); });
