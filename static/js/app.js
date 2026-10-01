/* ================================================================
   CCTV Monitoring — modul induk

     GET  /api/lines                     snapshot awal
     WS   /ws                            update realtime

   Berkas ini merangkai: sambungan ke server, penyaringan, perpindahan
   view, dan penggambaran kamera/detail/peta/analisa. Bagian yang berdiri
   sendiri tinggal di modulnya masing-masing:

     inti.js       pembantu kecil: esc, num, setText, warna mesin
     keadaan.js    data bersama dari snapshot terakhir (S)
     dialog.js     pengganti prompt/confirm/alert
     tema.js       terang / gelap / ikuti sistem
     operator.js   nama penanggung jawab + penutupan alert
     laporan.js    laporan shift
     tampil_mo.js  portal MO
     calibrate.js  kanvas kalibrasi zona & kotak lampu

   Update dari server tidak membangun ulang DOM: nilai di-patch di tempat
   supaya tidak berkedip dan scroll/hover tidak hilang.
   ================================================================ */
import {
  $, durasi, effClass, esc, fmt, jam, LAMPU_SAH, num, setClass, setHTML,
  setText, setWidth, statusText, unitClass, unitTitle,
} from "./inti.js";
import { byId, S } from "./keadaan.js";
import { dlgConfirm, dlgInfo, dlgOpen } from "./dialog.js";
import { temaMuat, bukaPengaturan } from "./tema.js";
import {
  askOperator, operatorLupakan, operatorNama, operatorPeriksaPeriode,
  resolveAlert,
} from "./operator.js";
import {
  anaTabAktif, laporanHubungkan, loadPeriods, loadReport, renderReport,
  setAnaTab,
} from "./laporan.js";
import { renderMoPortal } from "./tampil_mo.js";
import {
  CAL, calBindControls, calClose, calDraw, calHubungkan, calShowSaved,
} from "./calibrate.js";

/* ================================================================
   CCTV Monitoring — frontend
     GET  /api/lines                     snapshot awal
     WS   /ws                            update realtime
     POST /api/alerts/{id}/resolve       tindak lanjut alert

   Update dari server tidak membangun ulang DOM: nilai di-patch
   di tempat supaya tidak berkedip dan scroll/hover tidak hilang.
   ================================================================ */

/* Data bersama ada di keadaan.js (objek S). Yang di bawah ini state yang
   HANYA dipakai berkas ini: pilihan view, penyaring, dan penanda DOM. */

/* ---------------- STATE ---------------- */
let view = "camera";                 // camera | detail | map | analysis
let sel = { group: null, line: null };
let openGroups = new Set();
let openAna = new Set();
let detailLine = null;
let filter = "all";
let query = "";
let lastSig = "";                    // struktur DOM terakhir yang dirender



/* ---------------- KONEKSI ---------------- */
let ws = null, retry = 0;

function setConn(state) {
  const dot = $("connDot");
  setClass(dot, "dot-live " + state);
  dot.title = state === "on" ? "Terhubung ke server"
            : state === "off" ? "Terputus — mencoba menyambung ulang"
            : "Menghubungkan...";
}

function applySnapshot(data) {
  S.GROUPS = data.groups || [];
  S.ALL = S.GROUPS.flatMap(g => g.lines);
  S.HALLS = data.halls || [];
  S.SUMMARY = data.summary || {};
  S.STREAM_MODE = data.stream_mode || "video";
  S.IMG_REFRESH = data.stream_img_refresh || 0;
  S.SHIFT = data.shift || {};
  S.SOURCE_MODE = data.source || "sim";
  S.VISION_LEASE = data.vision_lease || 60;
  operatorPeriksaPeriode();     // nama operator kedaluwarsa saat shift berganti
  if (!openGroups.size) S.GROUPS.forEach(g => openGroups.add(g.key));

  renderKPI();
  // struktur sama -> cukup patch nilainya, tidak bangun ulang DOM
  if (signature() === lastSig) patch();
  else render();
}

function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  ws = new WebSocket(`${proto}//${location.host}/ws`);
  ws.onopen = () => { retry = 0; setConn("on"); };
  ws.onmessage = e => {
    const data = JSON.parse(e.data);
    if (data.type === "snapshot") applySnapshot(data);
  };
  ws.onclose = () => {
    setConn("off");
    retry = Math.min(retry + 1, 6);
    setTimeout(connect, retry * 2000);
  };
  ws.onerror = () => ws.close();
}

setInterval(() => {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send("ping");
}, 25000);

/* ---------------- FILTER ---------------- */
function visibleLines() {
  let list = S.ALL;
  if (sel.line) list = S.ALL.filter(l => l.id === sel.line);
  else if (sel.group) list = S.ALL.filter(l => l.type === sel.group);
  if (filter === "run")   list = list.filter(l => l.status === "run");
  if (filter === "stop")  list = list.filter(l => l.status !== "run");
  if (filter === "alert") list = list.filter(l => l.alert);
  if (query) list = list.filter(l =>
    `${l.name} ${l.operator} ${l.machines.map(m => m.order_mo).join(" ")}`
      .toLowerCase().includes(query));
  return list;
}

/* tanda tangan struktur DOM: kalau berubah, perlu render ulang */
function signature() {
  return [
    view,
    detailLine,
    visibleLines().map(l => l.id).join(","),
    [...openAna].sort().join(","),
    [...openGroups].sort().join(","),
    view === "detail" && byId(detailLine)
      ? (byId(detailLine).alert ? "A" : "-") +
        byId(detailLine).mo_groups.map(g => g.mo + g.machines.join("")).join("|")
      : "",
  ].join("::");
}

/* Render isi kotak kamera sesuai mode restreamer.
   grid=true  -> pakai sub-stream resolusi rendah (hemat bandwidth & CPU) */
function camInner(line, grid) {
  const url = grid && line.cam.stream_grid ? line.cam.stream_grid : line.cam.stream;
  if (!line.cam.online || !url) return `<div class="noise"></div>`;

  if (S.STREAM_MODE === "iframe") {
    return `<iframe src="${esc(url)}" frameborder="0" allow="autoplay"
              scrolling="no" loading="lazy"></iframe>`;
  }
  if (S.STREAM_MODE === "img") {
    // crossorigin: supaya kalibrasi bisa membaca warna piksel dari canvas.
    // Butuh header Access-Control-Allow-Origin dari sumber stream.
    return `<img src="${esc(url)}" alt="${esc(line.cam.id)}" data-refresh="${esc(url)}"
              crossorigin="anonymous">`;
  }
  return `<video src="${esc(url)}" muted autoplay playsinline
            preload="none" disablepictureinpicture></video>`;
}

/* ---------------- ROUTING VIEW ---------------- */
function setView(v) {
  if (view === v) return;
  if (typeof calClose === "function" && v !== "detail") calClose();
  view = v;
  ["viewCamera", "viewDetail", "viewMap", "viewMo", "viewAnalysis"]
    .forEach(id => $(id).hidden = true);
  const active = { camera: "viewCamera", detail: "viewDetail",
                   map: "viewMap", mo: "viewMo", analysis: "viewAnalysis" }[v];
  const el = $(active);
  el.hidden = false;
  el.classList.remove("fade");           // restart animasi masuk
  void el.offsetWidth;
  el.classList.add("fade");

  // Portal MO memakai lebar penuh: isinya tabel 18 line x 10 mesin, dan
  // KPI di atas tidak ada gunanya saat sedang mengetik nomor order.
  const penuh = (v === "map" || v === "mo");
  document.querySelector(".analytics").hidden = (v === "detail" || v === "mo");
  $("sidebar").hidden = penuh;
  document.querySelector(".layout").classList.toggle("no-side", penuh);

  document.querySelectorAll(".rail-btn[data-view]").forEach(b => {
    const target = v === "detail" ? "camera" : v;
    b.classList.toggle("active", b.dataset.view === target);
  });
  render();
}

/* ================================================================
   RENDER — membangun DOM (dipanggil saat struktur berubah)
   ================================================================ */
function render() {
  renderKPI();
  if (view !== "map" && view !== "mo") renderTree();
  if (view === "camera")        renderCards();
  else if (view === "detail")   renderDetail();
  else if (view === "map")      renderMap();
  else if (view === "mo")       renderMoPortal();
  else if (view === "analysis" && anaTabAktif() === "live") renderAnalysis();
  lastSig = signature();
}

/* ---------------- SIDEBAR ---------------- */
function renderTree() {
  const tree = $("tree");
  setHTML(tree, S.GROUPS.map(g => `
    <div class="grp${openGroups.has(g.key) ? " open" : ""}" data-grp="${esc(g.key)}">
      <div class="grp-head">
        <span><span class="caret">&#9654;</span> ${esc(g.label)}</span>
        <span class="cnt">${num(g.lines.length)} line</span>
      </div>
      <div class="grp-body">
        ${g.lines.map(l => `
          <div class="mch${sel.line === l.id ? " active" : ""}" data-line="${esc(l.id)}">
            <i class="s-${esc(l.status)}"></i>${esc(l.name)}
            ${l.alert ? '<b class="mch-alert">!</b>' : ""}
          </div>`).join("")}
      </div>
    </div>`).join(""));

  tree.querySelectorAll(".grp-head").forEach(h => {
    h.onclick = () => {
      const key = h.parentElement.dataset.grp;
      if (openGroups.has(key) && sel.group === key) {
        openGroups.delete(key); sel = { group: null, line: null };
      } else {
        openGroups.add(key); sel = { group: key, line: null };
      }
      render();
    };
  });
  tree.querySelectorAll(".mch").forEach(el => {
    el.onclick = e => {
      e.stopPropagation();
      // Klik nama line di sidebar langsung membuka layar kameranya, bukan
      // sekadar menyaring grid jadi satu kartu kecil. Itu yang diharapkan
      // orang saat menunjuk satu line: mau melihat kameranya.
      sel = { group: null, line: el.dataset.line };
      openDetail(el.dataset.line);
    };
  });
}


/* ---------------- KPI ---------------- */
function renderKPI() {
  const s = S.SUMMARY;
  if (!s.total_line) return;
  setText($("kpiLine"), s.total_line);
  setText($("kpiLineSub"), `${s.mesin_total} mesin • ${s.mesin_per_line}/line`);
  setText($("kpiRun"), s.run);
  setText($("kpiRunPct"), `${s.run_pct}% line aktif`);
  setText($("kpiIdle"), s.idle);
  setText($("kpiStop"), s.stop);
  setHTML($("kpiMesin"), `${s.mesin_run}<small> / ${s.mesin_total}</small>`);
  setWidth($("mesinBar"), s.mesin_pct);
  setText($("kpiEff"), `${s.eff}%`);
  setWidth($("effBar"), s.eff);
  setHTML($("kpiOut"), `${fmt(s.output)}<small> m</small>`);
  setText($("kpiOutTrend"), `${s.output_pct}% dari target ${fmt(s.target)} m`);

  if (S.SHIFT.name) {
    setText($("shiftName"), S.SHIFT.name);
    setText($("shiftTime"), `${S.SHIFT.started_at}–${S.SHIFT.next_at}`);
    $("shiftBox").title =
      `Shift ${S.SHIFT.name} • berjalan ${S.SHIFT.elapsed_min} menit • ` +
      `sisa ${S.SHIFT.remaining_min} menit • penghitung direset tiap ${S.SHIFT.reset_mode}`;
  }

  const badge = $("bellBadge");
  setText(badge, s.alerts);
  badge.hidden = !s.alerts;
  $("bell").classList.toggle("has-alert", !!s.alerts);
}

/* ---------------- VIEW: CAMERA ---------------- */
function renderCards() {
  const grid = $("grid");
  const list = visibleLines();

  setText($("viewTitle"),
    sel.line  ? (byId(sel.line) || {}).name || "Line"
  : sel.group ? `${(S.GROUPS.find(g => g.key === sel.group) || {}).label} — semua line`
  : "Semua Line");
  setText($("viewCount"), `${list.length} line`);

  if (!list.length) {
    setHTML(grid, `<div class="empty">Tidak ada line yang cocok.</div>`);
    return;
  }

  setHTML(grid, list.map(l => `
    <div class="card${l.alert ? " alerting" : ""}" data-line="${esc(l.id)}">
      <div class="card-head">
        <span class="card-name"><i class="s-${esc(l.status)}"></i>${esc(l.name)}</span>
        <span class="pill ${esc(l.status)}">${esc(l.status_text)}</span>
      </div>
      <div class="cam${l.cam.online ? "" : " offline"}">
        ${camInner(l, true)}
        <span class="cam-time">${nowStr()}</span>
        <span class="rec">&#9679; REC</span>
        <span class="cam-stale"${["basi","kosong"].includes(visionState(l))
          ? "" : " hidden"}>&#9888; data AI terhenti</span>
        <span class="cam-alert"${l.alert ? "" : " hidden"}>&#9888; ${
          l.alert ? esc(l.alert.label) : ""}</span>
      </div>
      <div class="card-foot">
        <span class="mo-dots">${l.mo_groups.map((g, i) =>
          `<b class="mo-c${i % 5}" title="${esc(g.mo)} — ${num(g.count)} mesin">${
            esc(g.mo.replace("MO-", ""))}</b>`
        ).join("")}</span>
        <span class="card-run">${num(l.mesin_run)}/${num(l.mesin)} jalan</span>
      </div>
    </div>`).join(""));

  grid.querySelectorAll(".card").forEach(el => {
    el.onclick = () => openDetail(el.dataset.line);
  });
}

function patchCards() {
  visibleLines().forEach(l => {
    const card = document.querySelector(`.card[data-line="${l.id}"]`);
    const st = card && card.querySelector(".cam-stale");
    if (st) st.hidden = !["basi", "kosong"].includes(visionState(l));
    if (!card) return;
    card.classList.toggle("alerting", !!l.alert);
    setClass(card.querySelector(".card-name i"), `s-${l.status}`);
    const pill = card.querySelector(".card-head .pill");
    setClass(pill, `pill ${l.status}`);
    setText(pill, l.status_text);
    const ca = card.querySelector(".cam-alert");
    ca.hidden = !l.alert;
    if (l.alert) setText(ca, `⚠ ${l.alert.label}`);
    setText(card.querySelector(".card-run"), `${l.mesin_run}/${l.mesin} jalan`);
  });
}

/* ---------------- VIEW: DETAIL ---------------- */
function openDetail(id) {
  if (typeof calClose === "function" && detailLine !== id) calClose();
  detailLine = id;
  if (view === "detail") render(); else setView("detail");
}

/* daftar chip mesin milik satu MO */
function moChips(line, g) {
  return g.machines.map(no => {
    const m = line.machines.find(x => x.no === no);
    return `<span class="chip ${esc(m.status)} ${esc(unitClass(m))}"
      data-m="${num(no)}" title="${esc(unitTitle(m))}">
      <i></i>${String(no).padStart(2, "0")}</span>`;
  }).join("");
}

function renderDetail() {
  const l = byId(detailLine);
  if (!l) { setView("camera"); return; }

  setText($("detailTitle"), `${l.name} — ${l.area}`);
  setText($("detailCam"), l.cam.id);

  $("logDownload").href = `/api/log/${l.id}.txt`;
  refreshLogCount(l.id);

  const cal = l.cal || { zone: [], machines: [] };
  const badge = $("calBadge");
  const done = cal.zone.length >= 3 || cal.machines.length > 0;
  badge.hidden = false;
  setClass(badge, "cal-badge " + (done ? "ok" : "none"));
  setText(badge, done
    ? `terkalibrasi: ${cal.zone.length} titik zona, ${cal.machines.length} lampu`
    : "belum dikalibrasi");

  const cam = $("detailCam2");
  setClass(cam, "cam big" + (l.cam.online ? "" : " offline") + (l.alert ? " alerting" : ""));
  const holder = cam.querySelector(".noise, video, iframe, img");
  if (holder) holder.outerHTML = camInner(l, false);
  if (typeof calDraw === "function" && typeof calShowSaved === "function") {
    CAL.on ? calDraw() : calShowSaved(l);
  }

  // ---- kelompok MO ----
  setText($("moCount"), `${l.mo_groups.length} MO • ${l.mesin} mesin`);
  setHTML($("moList"), l.mo_groups.map((g, i) => `
    <div class="mo" data-mo="${esc(g.mo)}">
      <div class="mo-head">
        <span class="mo-tag mo-c${i % 5}">${esc(g.mo)}</span>
        <span class="mo-n">${num(g.count)} mesin</span>
        <div class="mo-stats">
          <div>Jalan<b class="mo-run">${num(g.running)}/${num(g.count)}</b></div>
          <div>Efisiensi<b class="mo-eff">${num(g.eff)}%</b></div>
          <div>Output<b class="mo-out">${fmt(g.output)} m</b></div>
          <div>Stop<b class="mo-stop">${num(g.stops)}x</b></div>
        </div>
      </div>
      <div class="mo-chips">${moChips(l, g)}</div>
    </div>`).join(""));

  renderPanel(l);
}

function patchDetail() {
  const l = byId(detailLine);
  if (!l) return;

  const cam = $("detailCam2");
  setClass(cam, "cam big" + (l.cam.online ? "" : " offline") + (l.alert ? " alerting" : ""));

  l.mo_groups.forEach(g => {
    const box = document.querySelector(`.mo[data-mo="${g.mo}"]`);
    if (!box) return;
    setText(box.querySelector(".mo-run"), `${g.running}/${g.count}`);
    setText(box.querySelector(".mo-eff"), `${g.eff}%`);
    setText(box.querySelector(".mo-out"), `${fmt(g.output)} m`);
    setText(box.querySelector(".mo-stop"), `${g.stops}x`);
    g.machines.forEach(no => {
      const m = l.machines.find(x => x.no === no);
      const chip = box.querySelector(`.chip[data-m="${no}"]`);
      if (chip) {
        setClass(chip, `chip ${m.status} ${unitClass(m)}`);
        chip.title = unitTitle(m);
      }
    });
  });

  patchPanel(l);
}

function infoBlock(l) {
  return `
    <div class="panel-sec">
      <h4>Info Line</h4>
      <div class="kv"><span>Operator</span><b data-f="operator">${esc(l.operator)}</b></div>
      <div class="kv"><span>Shift</span><b data-f="shift">${esc(l.shift)}</b></div>
      <div class="kv"><span>Order MO</span><b data-f="mo">${num(l.mo_groups.length)} MO aktif</b></div>
      <div class="kv"><span>Status</span><b data-f="status" class="t-${esc(l.status)}">${
        esc(l.status_text)}</b></div>
      <div class="kv"><span>RPM</span><b data-f="rpm">${esc(l.rpm || "-")}</b></div>
      <div class="kv"><span>Efisiensi</span><b data-f="eff">${num(l.eff)}%</b></div>
      <div class="kv"><span>Output</span><b data-f="out">${fmt(l.output)} m</b></div>
      <div class="kv"><span>Stop</span><b data-f="stops">${num(l.stops)}x</b></div>
      <div class="kv"><span>Mesin Jalan</span><b data-f="run">${num(l.mesin_run)} / ${num(l.mesin)}</b></div>
    </div>`;
}

/* Apakah AI worker masih mengirim data untuk line ini?
 *
 * Panel lama SELALU berkata "Kamera aktif, AI tidak menemukan kejadian" —
 * termasuk saat worker mati, kamera lepas, atau zona belum dikalibrasi.
 * Kalimat itu bukan sekadar kosong, melainkan menenangkan secara keliru:
 * layar meyakinkan operator bahwa line diawasi justru ketika tidak.
 *
 *   "ok"    data segar
 *   "basi"  pernah mengirim, lalu berhenti  <- worker/kamera bermasalah
 *   "kosong" belum pernah mengirim sama sekali
 *   "sim"   mode simulasi: memang tidak ada AI, bukan kerusakan
 */
function visionState(l) {
  const umur = l.vision_age;
  // Data dari AI dinilai BASI tanpa memandang S.SOURCE_MODE. Dulu di sini ada
  // jalan pintas `if (S.SOURCE_MODE !== "live") return "sim"`, dan itu menjadi
  // berbahaya begitu simulator berhenti menimpa status mesin: mode "sim"
  // sekarang membawa pembacaan lampu SUNGGUHAN, jadi worker yang mati
  // meninggalkan warna terakhir membeku di layar tanpa satu pun peringatan.
  // (LiveSource juga masih NotImplementedError, jadi "live" tak tercapai —
  // jalan pintas itu membuat peringatannya jadi kode mati.)
  if (umur !== null && umur !== undefined) {
    return umur > S.VISION_LEASE ? "basi" : "ok";
  }
  return S.SOURCE_MODE === "live" ? "kosong" : "sim";
}

function panelNoVision(l, state) {
  const basi = state === "basi";
  const judul = basi ? "DATA AI TERHENTI" : "BELUM ADA DATA AI";
  const pesan = basi
    ? `AI worker berhenti mengirim data untuk line ini sejak
       ${durasi(l.vision_age)} lalu. Selama ini line TIDAK terpantau —
       angka di bawah adalah nilai terakhir sebelum data berhenti.`
    : `AI worker belum pernah mengirim data untuk line ini. Periksa apakah
       worker berjalan dan line ini ada di ai/zones.json.`;
  return `
    <div class="panel-head stale">
      <svg viewBox="0 0 24 24" class="ico"><path d="M12 9v4"/><path d="M12 17h.01"/>
        <circle cx="12" cy="12" r="9"/></svg>
      ${judul}
    </div>
    <div class="panel-body">
      <div class="no-alert stale">
        <div class="no-alert-ico">&#9888;</div>
        <p>${pesan}</p>
        <small>Periksa: <code>journalctl -u cctv-ai -f</code></small>
      </div>
      ${infoBlock(l)}
    </div>`;
}

function renderPanel(l) {
  const p = $("alertPanel");
  const a = l.alert;
  const vs = visionState(l);

  if (!a && (vs === "basi" || vs === "kosong")) {
    setClass(p, "panel stale");
    p.dataset.state = vs;
    setHTML(p, panelNoVision(l, vs));
    return;
  }

  if (!a) {
    setClass(p, "panel");
    p.dataset.state = "clear";
    setHTML(p, `
      <div class="panel-head clear">
        <svg viewBox="0 0 24 24" class="ico"><path d="M20 6 9 17l-5-5"/></svg>
        TIDAK ADA DETEKSI
      </div>
      <div class="panel-body">
        <div class="no-alert">
          <div class="no-alert-ico">&#128065;</div>
          <p>${vs === "sim"
              ? "Mode simulasi — angka di layar ini bukan data pabrik."
              : "Kamera aktif, AI tidak menemukan kejadian mencurigakan di line ini."}</p>
          <small id="lastCheck">Terakhir dicek ${nowStr()}</small>
        </div>
        ${infoBlock(l)}
      </div>`);
    return;
  }

  setClass(p, "panel alert");
  p.dataset.state = "alert";
  setHTML(p, `
    <div class="panel-head danger">
      <svg viewBox="0 0 24 24" class="ico"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg>
      ALERT AKTIF
    </div>
    <div class="panel-body">
      <div class="panel-sec">
        <h4>Info Deteksi</h4>
        <div class="kv"><span>Zona</span><b>${esc(a.zone)}</b></div>
        <div class="kv"><span>Aktivitas</span><b>${esc(a.activity)}</b></div>
        <div class="kv"><span>Waktu Deteksi</span><b>${esc(a.detected_at)}</b></div>
      </div>

      <div class="panel-sec">
        <h4>Rincian Deteksi</h4>
        <div class="conf">
          <span>${esc(a.label)}</span>
          <b class="conf-badge">${num(a.confidence)}%</b>
        </div>
        <div class="conf-bar"><span style="width:${num(a.confidence)}%"></span></div>

        ${a.snapshots.map(t => `
          <div class="snap">
            <div class="snap-img"><div class="noise"></div></div>
            <div class="snap-meta"><span>Waktu</span><b>${esc(t)}</b></div>
          </div>`).join("")}

        <div class="kv"><span>Jenis Objek</span><b>${esc(a.object_type)}</b></div>
        <div class="kv"><span>Durasi</span><b>${num(a.duration)} detik</b></div>
        <div class="kv"><span>Tingkat</span>
          <b class="sev ${a.severity === "high" ? "high" : "med"}">${
            a.severity === "high" ? "Tinggi" : "Sedang"}</b></div>
      </div>

      ${infoBlock(l)}
    </div>
    <div class="panel-actions">
      <button class="btn-ghost" data-act="false">False Alarm</button>
      <button class="btn-danger" data-act="accept">Tindak Lanjuti</button>
    </div>`);

  p.querySelectorAll("[data-act]").forEach(b => {
    b.onclick = () => resolveAlert(a.id, b.dataset.act);
  });
}

/* panel: cukup perbarui angkanya, jangan bangun ulang saat operator sedang membaca */
function patchPanel(l) {
  const p = $("alertPanel");
  const vs = visionState(l);
  const perlu = l.alert ? "alert"
              : (vs === "basi" || vs === "kosong") ? vs : "clear";
  if (p.dataset.state !== perlu) { renderPanel(l); return; }

  const set = (f, v) => setText(p.querySelector(`[data-f="${f}"]`), v);
  set("operator", l.operator);
  set("shift", l.shift);
  set("mo", `${l.mo_groups.length} MO aktif`);
  set("rpm", l.rpm || "-");
  set("eff", `${l.eff}%`);
  set("out", `${fmt(l.output)} m`);
  set("stops", `${l.stops}x`);
  set("run", `${l.mesin_run} / ${l.mesin}`);
  const st = p.querySelector('[data-f="status"]');
  if (st) { setText(st, l.status_text); setClass(st, `t-${l.status}`); }
  const lc = $("lastCheck");
  if (lc) setText(lc, `Terakhir dicek ${nowStr()}`);
  if (Date.now() - (patchPanel._t || 0) > 15000) {
    patchPanel._t = Date.now();
    refreshLogCount(l.id);            // jangan tiap 5 detik, cukup 15
  }
}




/* ---------------- CATATAN DETEKSI (pengumpulan data uji) ---------------- */
async function refreshLogCount(lineId) {
  const el = $("logCount");
  try {
    const d = await fetch("/api/log/lines").then(r => r.json());
    if (d.mode === "off") { el.hidden = true; return; }
    const row = (d.lines || []).find(x => x.line_id === lineId);
    const n = row ? row.n : 0;
    el.hidden = false;
    setClass(el, "log-count" + (n ? " ada" : ""));
    setText(el, n ? `${fmt(n)} episode tercatat` : "belum ada catatan");
  } catch (e) { el.hidden = true; }
}

async function clearLog() {
  const l = byId(detailLine);
  if (!l) return;
  const ya = await dlgConfirm(
    `Kosongkan catatan ${l.name}?`,
    "Dipakai untuk memulai sesi pengujian baru. Catatan line lain tidak " +
    "terpengaruh, dan tindakan ini tidak bisa dibatalkan.",
    "Kosongkan");
  if (!ya) return;
  try {
    const r = await fetch(`/api/log/${l.id}`, { method: "DELETE" }).then(x => x.json());
    refreshLogCount(l.id);
    await dlgInfo("Catatan dikosongkan",
                  `${l.name}: ${fmt(r.dihapus)} baris dihapus.`);
  } catch (e) {
    await dlgInfo("Gagal", "Catatan tidak bisa dikosongkan. Coba lagi.");
  }
}

/* ---------------- VIEW: MAP ---------------- */
function renderMap() {
  const map = $("map");
  const withAlert = S.ALL.filter(l => l.alert).length;
  setText($("mapCount"),
    `${S.ALL.length} line • ${S.ALL.length} kamera${withAlert ? ` • ${withAlert} alert` : ""}`);

  const halls = S.HALLS.map(h => `
    <div class="hall" style="left:${num(h.x)}%;top:${num(h.y)}%;width:${num(h.w)}%;height:${num(h.h)}%">
      <span class="hall-label">${esc(h.label)}</span>
    </div>`).join("");

  const lines = S.ALL.map(l => `
    <div class="mline ${esc(l.status)}${l.alert ? " alerting" : ""}" data-line="${esc(l.id)}"
         style="left:${num(l.pos.x)}%;top:${num(l.pos.y)}%;width:${num(l.pos.w)}%;height:${num(l.pos.h)}%"
         title="${esc(l.name)} — ${esc(l.status_text)} — operator ${esc(l.operator)}">
      <div class="mline-top">
        <span class="mline-name">${esc(l.name)}</span>
        <span class="mcam ${l.cam.online ? "" : "off"}">&#9679;</span>
      </div>
      <div class="mline-units">
        ${l.machines.map(m => `<i class="${esc(unitClass(m))}" data-m="${num(m.no)}"
            title="${esc(unitTitle(m))}"></i>`).join("")}
      </div>
      <span class="mline-alert"${l.alert ? "" : " hidden"}>&#9888;</span>
    </div>`).join("");

  setHTML(map, halls + lines);
  map.querySelectorAll(".mline").forEach(el => {
    el.onclick = () => openDetail(el.dataset.line);
  });
}

function patchMap() {
  S.ALL.forEach(l => {
    const el = document.querySelector(`.mline[data-line="${l.id}"]`);
    if (!el) return;
    setClass(el, `mline ${l.status}${l.alert ? " alerting" : ""}`);
    el.title = `${l.name} — ${l.status_text} — operator ${l.operator}`;
    el.querySelector(".mline-alert").hidden = !l.alert;
    l.machines.forEach(m => {
      const u = el.querySelector(`.mline-units i[data-m="${m.no}"]`);
      if (u) { setClass(u, `u-${m.status}`); u.title = `${m.name} — ${m.order_mo}`; }
    });
  });
  const withAlert = S.ALL.filter(l => l.alert).length;
  setText($("mapCount"),
    `${S.ALL.length} line • ${S.ALL.length} kamera${withAlert ? ` • ${withAlert} alert` : ""}`);
}

/* ---------------- VIEW: ANALYSIS ---------------- */
function machineRow(m) {
  return `
    <tr data-m="${num(m.no)}">
      <td class="m-name"><i class="s-${esc(m.status)}"></i>${esc(m.name)}</td>
      <td class="m-mo"><span class="mo-tag sm">${esc(m.order_mo)}</span></td>
      <td><span class="pill ${esc(m.status)}">${esc(statusText(m.status))}</span></td>
      <td class="m-rpm">${esc(m.rpm || "-")}</td>
      <td class="w">
        <div class="mini-bar"><span class="${effClass(m.eff)}" style="width:${num(m.eff)}%"></span></div>
        <em>${num(m.eff)}%</em>
      </td>
      <td class="m-out">${fmt(m.output)} m</td>
      <td class="m-stop">${num(m.stops)}x</td>
      <td class="m-down">${num(m.downtime)} mnt</td>
    </tr>`;
}

function renderAnalysis() {
  const box = $("analysis");
  const list = visibleLines();
  setText($("anaCount"),
    `${list.length} line • ${list.length * (S.SUMMARY.mesin_per_line || 10)} mesin`);

  if (!list.length) {
    setHTML(box, `<div class="empty">Tidak ada line yang cocok.</div>`);
    return;
  }

  setHTML(box, list.map(l => {
    const open = openAna.has(l.id);
    // urutkan mesin per MO supaya pengelompokan terlihat
    const ordered = l.mo_groups.flatMap(g =>
      g.machines.map(no => l.machines.find(m => m.no === no)));
    return `
    <div class="ana${open ? " open" : ""}" data-line="${esc(l.id)}">
      <div class="ana-head">
        <span class="caret">&#9654;</span>
        <span class="ana-name"><i class="s-${esc(l.status)}"></i>${esc(l.name)}</span>
        <span class="pill ${esc(l.status)}">${esc(l.status_text)}</span>
        <span class="ana-alert"${l.alert ? "" : " hidden"}>&#9888; ${
          l.alert ? esc(l.alert.label) : ""}</span>
        <span class="mo-dots">${l.mo_groups.map((g, i) =>
          `<b class="mo-c${i % 5}" title="${esc(g.mo)} — mesin ${esc(g.machines.join(", "))}">${
            esc(g.mo.replace("MO-", ""))}</b>`
        ).join("")}</span>
        <div class="ana-stats">
          <div>Operator<b class="a-op">${esc(l.operator)}</b></div>
          <div>Mesin Jalan<b class="a-run">${num(l.mesin_run)}/${num(l.mesin)}</b></div>
          <div>RPM<b class="a-rpm">${esc(l.rpm || "-")}</b></div>
          <div>Efisiensi<b class="a-eff">${num(l.eff)}%</b></div>
          <div>Output<b class="a-out">${fmt(l.output)} m</b></div>
          <div>Stop<b class="a-stop">${num(l.stops)}x</b></div>
        </div>
      </div>
      <div class="ana-body">
        <table class="ana-table">
          <thead>
            <tr><th>Mesin</th><th>Order MO</th><th>Status</th><th>RPM</th>
                <th class="w">Efisiensi</th><th>Output</th><th>Stop</th><th>Downtime</th></tr>
          </thead>
          <tbody>${ordered.map(machineRow).join("")}</tbody>
        </table>
      </div>
    </div>`;
  }).join(""));

  box.querySelectorAll(".ana-head").forEach(h => {
    h.onclick = () => {
      const id = h.parentElement.dataset.line;
      openAna.has(id) ? openAna.delete(id) : openAna.add(id);
      render();
    };
  });
}

function patchAnalysis() {
  visibleLines().forEach(l => {
    const el = document.querySelector(`.ana[data-line="${l.id}"]`);
    if (!el) return;
    setClass(el.querySelector(".ana-name i"), `s-${l.status}`);
    const pill = el.querySelector(".ana-head .pill");
    setClass(pill, `pill ${l.status}`);
    setText(pill, l.status_text);
    const al = el.querySelector(".ana-alert");
    al.hidden = !l.alert;
    if (l.alert) setText(al, `⚠ ${l.alert.label}`);

    setText(el.querySelector(".a-op"), l.operator);
    setText(el.querySelector(".a-run"), `${l.mesin_run}/${l.mesin}`);
    setText(el.querySelector(".a-rpm"), l.rpm || "-");
    setText(el.querySelector(".a-eff"), `${l.eff}%`);
    setText(el.querySelector(".a-out"), `${fmt(l.output)} m`);
    setText(el.querySelector(".a-stop"), `${l.stops}x`);

    if (!openAna.has(l.id)) return;      // baris tabel hanya kalau terbuka
    l.machines.forEach(m => {
      const tr = el.querySelector(`tr[data-m="${m.no}"]`);
      if (!tr) return;
      setClass(tr.querySelector(".m-name i"), `s-${m.status}`);
      const p = tr.querySelector(".pill");
      setClass(p, `pill ${m.status}`);
      setText(p, statusText(m.status));
      setText(tr.querySelector(".m-rpm"), m.rpm || "-");
      setText(tr.querySelector(".m-out"), `${fmt(m.output)} m`);
      setText(tr.querySelector(".m-stop"), `${m.stops}x`);
      setText(tr.querySelector(".m-down"), `${m.downtime} mnt`);
      const bar = tr.querySelector(".mini-bar span");
      setClass(bar, effClass(m.eff));
      setWidth(bar, m.eff);
      setText(tr.querySelector("em"), `${m.eff}%`);
    });
  });
}

/* ================================================================
   PATCH — perbarui nilai tanpa membangun ulang DOM
   ================================================================ */
function patch() {
  if (view !== "map") patchTree();
  if (view === "camera")        patchCards();
  else if (view === "detail")   patchDetail();
  else if (view === "map")      patchMap();
  else if (view === "analysis" && anaTabAktif() === "live") patchAnalysis();
}

function patchTree() {
  S.ALL.forEach(l => {
    const el = document.querySelector(`.mch[data-line="${l.id}"]`);
    if (!el) return;
    setClass(el.querySelector("i"), `s-${l.status}`);
    let mark = el.querySelector(".mch-alert");
    if (l.alert && !mark) {
      mark = document.createElement("b");
      mark.className = "mch-alert";
      mark.textContent = "!";
      el.appendChild(mark);
    } else if (!l.alert && mark) {
      mark.remove();
    }
  });
}

/* ---------------- EVENTS ---------------- */
function bindEvents() {
  document.querySelectorAll(".rail-btn[data-view]").forEach(b => {
    b.onclick = () => setView(b.dataset.view);
  });

  $("detailBack").onclick = () => { detailLine = null; setView("camera"); };
  $("logClear").onclick = clearLog;
  if (typeof calBindControls === "function") calBindControls();

  document.querySelectorAll(".view-actions .btn[data-filter]").forEach(b => {
    b.onclick = () => {
      document.querySelectorAll(".view-actions .btn[data-filter]")
        .forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      filter = b.dataset.filter;
      render();
    };
  });

  $("tabLive").onclick   = () => setAnaTab("live");
  $("tabReport").onclick = () => setAnaTab("report");
  $("repPeriod").onchange = e => { repKey = e.target.value; loadReport(); };
  $("repPrint").onclick = () => window.print();
  $("anaExpand").onclick   = () => { visibleLines().forEach(l => openAna.add(l.id)); render(); };
  $("anaCollapse").onclick = () => { openAna.clear(); render(); };

  $("btnSetting").onclick = bukaPengaturan;

  $("bell").onclick = () => {
    filter = "alert";
    document.querySelectorAll(".view-actions .btn[data-filter]").forEach(x =>
      x.classList.toggle("active", x.dataset.filter === "alert"));
    if (view === "camera") render(); else setView("camera");
  };

  let t = null;
  $("search").oninput = e => {
    clearTimeout(t);
    t = setTimeout(() => { query = e.target.value.toLowerCase().trim(); render(); }, 150);
  };

  document.addEventListener("keydown", e => {
    if (e.key !== "Escape" || view !== "detail") return;
    if (CAL.on) { calClose(); return; }
    detailLine = null; setView("camera");
  });
}

/* ---------------- REFRESH SNAPSHOT (mode img) ---------------- */
setInterval(() => {
  if (S.STREAM_MODE !== "img" || !S.IMG_REFRESH) return;
  const stamp = Date.now();
  document.querySelectorAll(".cam img[data-refresh]").forEach(img => {
    const base = img.dataset.refresh;
    img.src = base + (base.includes("?") ? "&" : "?") + "_t=" + stamp;
  });
}, Math.max(1, S.IMG_REFRESH) * 1000);

/* ---------------- JAM ---------------- */
function nowStr() { return new Date().toLocaleTimeString("id-ID", { hour12: false }); }

setInterval(() => {
  setText($("clock"), new Date().toLocaleString("id-ID", { hour12: false }));
  document.querySelectorAll(".cam-time").forEach(el => setText(el, nowStr()));
  const st = $("detailStamp");
  if (st && view === "detail") {
    setText(st, `${new Date().toLocaleDateString("id-ID",
      { day: "numeric", month: "long", year: "numeric" })}  |  ${nowStr()}`);
  }
}, 1000);

/* ---------------- INIT ---------------- */
async function boot() {
  temaMuat();
  // Sambungkan modul yang butuh sesuatu dari sini. Dilakukan sekali di awal
  // supaya tidak ada modul yang mengimpor app.js — impor melingkar membuat
  // kedua berkas gagal dimuat, dan halaman kosong tanpa pesan yang jelas.
  calHubungkan({
    line: () => detailLine,
    gambarUlang: () => { if (view === "detail") renderDetail(); },
    diDetail: () => view === "detail",
  });
  laporanHubungkan(render);
  bindEvents();
  try {
    const o = await fetch("/api/operators").then(r => r.json());
    S.OPERATOR_LIST = o.names || [];
  } catch (e) { /* daftar operator opsional */ }
  try {
    const res = await fetch("/api/lines");
    applySnapshot(await res.json());
  } catch (err) {
    setHTML($("grid"), `<div class="empty">Gagal mengambil data dari server.</div>`);
  }
  connect();
}

boot();
