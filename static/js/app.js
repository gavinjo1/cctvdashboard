/* ================================================================
   CCTV Monitoring — frontend
     GET  /api/lines                     snapshot awal
     WS   /ws                            update realtime
     POST /api/alerts/{id}/resolve       tindak lanjut alert

   Update dari server tidak membangun ulang DOM: nilai di-patch
   di tempat supaya tidak berkedip dan scroll/hover tidak hilang.
   ================================================================ */

let GROUPS = [], ALL = [], HALLS = [], SUMMARY = {};
let STREAM_MODE = "video", IMG_REFRESH = 2;

/* ---------------- STATE ---------------- */
let view = "camera";                 // camera | detail | map | analysis
let sel = { group: null, line: null };
let openGroups = new Set();
let openAna = new Set();
let detailLine = null;
let filter = "all";
let query = "";
let lastSig = "";                    // struktur DOM terakhir yang dirender

const $ = id => document.getElementById(id);
const fmt = n => (n || 0).toLocaleString("id-ID");
const byId = id => ALL.find(l => l.id === id);
const statusText = s => ({ run: "Running", idle: "Idle", stop: "Stop", off: "Offline" }[s] || s);
const effClass = e => e >= 80 ? "good" : e >= 60 ? "mid" : "bad";

/* tulis hanya kalau berubah — mencegah repaint & kedip */
function setText(el, val) {
  if (el && el.textContent !== String(val)) el.textContent = val;
}
function setHTML(el, val) {
  if (el && el.innerHTML !== val) el.innerHTML = val;
}
function setClass(el, cls) {
  if (el && el.className !== cls) el.className = cls;
}
function setWidth(el, pct) {
  const v = pct + "%";
  if (el && el.style.width !== v) el.style.width = v;
}

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
  GROUPS = data.groups || [];
  ALL = GROUPS.flatMap(g => g.lines);
  HALLS = data.halls || [];
  SUMMARY = data.summary || {};
  STREAM_MODE = data.stream_mode || "video";
  IMG_REFRESH = data.stream_img_refresh || 0;
  if (!openGroups.size) GROUPS.forEach(g => openGroups.add(g.key));

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
  let list = ALL;
  if (sel.line) list = ALL.filter(l => l.id === sel.line);
  else if (sel.group) list = ALL.filter(l => l.type === sel.group);
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

  if (STREAM_MODE === "iframe") {
    return `<iframe src="${url}" frameborder="0" allow="autoplay"
              scrolling="no" loading="lazy"></iframe>`;
  }
  if (STREAM_MODE === "img") {
    // crossorigin: supaya kalibrasi bisa membaca warna piksel dari canvas.
    // Butuh header Access-Control-Allow-Origin dari sumber stream.
    return `<img src="${url}" alt="${line.cam.id}" data-refresh="${url}"
              crossorigin="anonymous">`;
  }
  return `<video src="${url}" muted autoplay playsinline
            preload="none" disablepictureinpicture></video>`;
}

/* ---------------- ROUTING VIEW ---------------- */
function setView(v) {
  if (view === v) return;
  if (typeof calClose === "function" && v !== "detail") calClose();
  view = v;
  ["viewCamera", "viewDetail", "viewMap", "viewAnalysis"].forEach(id => $(id).hidden = true);
  const active = { camera: "viewCamera", detail: "viewDetail",
                   map: "viewMap", analysis: "viewAnalysis" }[v];
  const el = $(active);
  el.hidden = false;
  el.classList.remove("fade");           // restart animasi masuk
  void el.offsetWidth;
  el.classList.add("fade");

  document.querySelector(".analytics").hidden = (v === "detail");
  $("sidebar").hidden = (v === "map");
  document.querySelector(".layout").classList.toggle("no-side", v === "map");

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
  if (view !== "map") renderTree();
  if (view === "camera")        renderCards();
  else if (view === "detail")   renderDetail();
  else if (view === "map")      renderMap();
  else if (view === "analysis") renderAnalysis();
  lastSig = signature();
}

/* ---------------- SIDEBAR ---------------- */
function renderTree() {
  const tree = $("tree");
  setHTML(tree, GROUPS.map(g => `
    <div class="grp${openGroups.has(g.key) ? " open" : ""}" data-grp="${g.key}">
      <div class="grp-head">
        <span><span class="caret">&#9654;</span> ${g.label}</span>
        <span class="cnt">${g.lines.length} line</span>
      </div>
      <div class="grp-body">
        ${g.lines.map(l => `
          <div class="mch${sel.line === l.id ? " active" : ""}" data-line="${l.id}">
            <i class="s-${l.status}"></i>${l.name}
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
      sel = { group: null, line: el.dataset.line };
      render();
    };
  });
}

/* ---------------- KPI ---------------- */
function renderKPI() {
  const s = SUMMARY;
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
  : sel.group ? `${(GROUPS.find(g => g.key === sel.group) || {}).label} — semua line`
  : "Semua Line");
  setText($("viewCount"), `${list.length} line`);

  if (!list.length) {
    setHTML(grid, `<div class="empty">Tidak ada line yang cocok.</div>`);
    return;
  }

  setHTML(grid, list.map(l => `
    <div class="card${l.alert ? " alerting" : ""}" data-line="${l.id}">
      <div class="card-head">
        <span class="card-name"><i class="s-${l.status}"></i>${l.name}</span>
        <span class="pill ${l.status}">${l.status_text}</span>
      </div>
      <div class="cam${l.cam.online ? "" : " offline"}">
        ${camInner(l, true)}
        <span class="cam-time">${nowStr()}</span>
        <span class="rec">&#9679; REC</span>
        <span class="cam-alert"${l.alert ? "" : " hidden"}>&#9888; ${l.alert ? l.alert.label : ""}</span>
      </div>
      <div class="card-foot">
        <span class="mo-dots">${l.mo_groups.map((g, i) =>
          `<b class="mo-c${i % 5}" title="${g.mo} — ${g.count} mesin">${g.mo.replace("MO-", "")}</b>`
        ).join("")}</span>
        <span class="card-run">${l.mesin_run}/${l.mesin} jalan</span>
      </div>
    </div>`).join(""));

  grid.querySelectorAll(".card").forEach(el => {
    el.onclick = () => openDetail(el.dataset.line);
  });
}

function patchCards() {
  visibleLines().forEach(l => {
    const card = document.querySelector(`.card[data-line="${l.id}"]`);
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
    return `<span class="chip ${m.status}" data-m="${no}" title="${m.name} — ${statusText(m.status)} — ${m.eff}%">
      <i></i>${String(no).padStart(2, "0")}</span>`;
  }).join("");
}

function renderDetail() {
  const l = byId(detailLine);
  if (!l) { setView("camera"); return; }

  setText($("detailTitle"), `${l.name} — ${l.area}`);
  setText($("detailCam"), l.cam.id);

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
  if (typeof calDraw === "function" && CAL.on) calDraw();

  // ---- kelompok MO ----
  setText($("moCount"), `${l.mo_groups.length} MO • ${l.mesin} mesin`);
  setHTML($("moList"), l.mo_groups.map((g, i) => `
    <div class="mo" data-mo="${g.mo}">
      <div class="mo-head">
        <span class="mo-tag mo-c${i % 5}">${g.mo}</span>
        <span class="mo-n">${g.count} mesin</span>
        <div class="mo-stats">
          <div>Jalan<b class="mo-run">${g.running}/${g.count}</b></div>
          <div>Efisiensi<b class="mo-eff">${g.eff}%</b></div>
          <div>Output<b class="mo-out">${fmt(g.output)} m</b></div>
          <div>Stop<b class="mo-stop">${g.stops}x</b></div>
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
        setClass(chip, `chip ${m.status}`);
        chip.title = `${m.name} — ${statusText(m.status)} — ${m.eff}%`;
      }
    });
  });

  patchPanel(l);
}

function infoBlock(l) {
  return `
    <div class="panel-sec">
      <h4>Info Line</h4>
      <div class="kv"><span>Operator</span><b data-f="operator">${l.operator}</b></div>
      <div class="kv"><span>Shift</span><b data-f="shift">${l.shift}</b></div>
      <div class="kv"><span>Order MO</span><b data-f="mo">${l.mo_groups.length} MO aktif</b></div>
      <div class="kv"><span>Status</span><b data-f="status" class="t-${l.status}">${l.status_text}</b></div>
      <div class="kv"><span>RPM</span><b data-f="rpm">${l.rpm || "-"}</b></div>
      <div class="kv"><span>Efisiensi</span><b data-f="eff">${l.eff}%</b></div>
      <div class="kv"><span>Output</span><b data-f="out">${fmt(l.output)} m</b></div>
      <div class="kv"><span>Stop</span><b data-f="stops">${l.stops}x</b></div>
      <div class="kv"><span>Mesin Jalan</span><b data-f="run">${l.mesin_run} / ${l.mesin}</b></div>
    </div>`;
}

function renderPanel(l) {
  const p = $("alertPanel");
  const a = l.alert;

  if (!a) {
    setClass(p, "panel");
    setHTML(p, `
      <div class="panel-head clear">
        <svg viewBox="0 0 24 24" class="ico"><path d="M20 6 9 17l-5-5"/></svg>
        TIDAK ADA DETEKSI
      </div>
      <div class="panel-body">
        <div class="no-alert">
          <div class="no-alert-ico">&#128065;</div>
          <p>Kamera aktif, AI tidak menemukan kejadian mencurigakan di line ini.</p>
          <small id="lastCheck">Terakhir dicek ${nowStr()}</small>
        </div>
        ${infoBlock(l)}
      </div>`);
    return;
  }

  setClass(p, "panel alert");
  setHTML(p, `
    <div class="panel-head danger">
      <svg viewBox="0 0 24 24" class="ico"><path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/></svg>
      ALERT AKTIF
    </div>
    <div class="panel-body">
      <div class="panel-sec">
        <h4>Detection Info</h4>
        <div class="kv"><span>Zona</span><b>${a.zone}</b></div>
        <div class="kv"><span>Aktivitas</span><b>${a.activity}</b></div>
        <div class="kv"><span>Waktu Deteksi</span><b>${a.detected_at}</b></div>
      </div>

      <div class="panel-sec">
        <h4>Detection Details</h4>
        <div class="conf">
          <span>${a.label}</span>
          <b class="conf-badge">${a.confidence}%</b>
        </div>
        <div class="conf-bar"><span style="width:${a.confidence}%"></span></div>

        ${a.snapshots.map(t => `
          <div class="snap">
            <div class="snap-img"><div class="noise"></div></div>
            <div class="snap-meta"><span>Waktu</span><b>${t}</b></div>
          </div>`).join("")}

        <div class="kv"><span>Object Type</span><b>${a.object_type}</b></div>
        <div class="kv"><span>Durasi</span><b>${a.duration} detik</b></div>
        <div class="kv"><span>Tingkat</span>
          <b class="sev ${a.severity}">${a.severity === "high" ? "Tinggi" : "Sedang"}</b></div>
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
  const hasAlertNow = !!l.alert;
  const hasAlertDom = p.classList.contains("alert");
  if (hasAlertNow !== hasAlertDom) { renderPanel(l); return; }

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
}

async function resolveAlert(alertId, action) {
  try {
    await fetch(`/api/alerts/${alertId}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
  } catch (e) { /* snapshot berikutnya akan menyusul */ }
}

/* ---------------- VIEW: MAP ---------------- */
function renderMap() {
  const map = $("map");
  const withAlert = ALL.filter(l => l.alert).length;
  setText($("mapCount"),
    `${ALL.length} line • ${ALL.length} kamera${withAlert ? ` • ${withAlert} alert` : ""}`);

  const halls = HALLS.map(h => `
    <div class="hall" style="left:${h.x}%;top:${h.y}%;width:${h.w}%;height:${h.h}%">
      <span class="hall-label">${h.label}</span>
    </div>`).join("");

  const lines = ALL.map(l => `
    <div class="mline ${l.status}${l.alert ? " alerting" : ""}" data-line="${l.id}"
         style="left:${l.pos.x}%;top:${l.pos.y}%;width:${l.pos.w}%;height:${l.pos.h}%"
         title="${l.name} — ${l.status_text} — operator ${l.operator}">
      <div class="mline-top">
        <span class="mline-name">${l.name}</span>
        <span class="mcam ${l.cam.online ? "" : "off"}">&#9679;</span>
      </div>
      <div class="mline-units">
        ${l.machines.map(m => `<i class="u-${m.status}" data-m="${m.no}"
            title="${m.name} — ${m.order_mo}"></i>`).join("")}
      </div>
      <span class="mline-alert"${l.alert ? "" : " hidden"}>&#9888;</span>
    </div>`).join("");

  setHTML(map, halls + lines);
  map.querySelectorAll(".mline").forEach(el => {
    el.onclick = () => openDetail(el.dataset.line);
  });
}

function patchMap() {
  ALL.forEach(l => {
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
  const withAlert = ALL.filter(l => l.alert).length;
  setText($("mapCount"),
    `${ALL.length} line • ${ALL.length} kamera${withAlert ? ` • ${withAlert} alert` : ""}`);
}

/* ---------------- VIEW: ANALYSIS ---------------- */
function machineRow(m) {
  return `
    <tr data-m="${m.no}">
      <td class="m-name"><i class="s-${m.status}"></i>${m.name}</td>
      <td class="m-mo"><span class="mo-tag sm">${m.order_mo}</span></td>
      <td><span class="pill ${m.status}">${statusText(m.status)}</span></td>
      <td class="m-rpm">${m.rpm || "-"}</td>
      <td class="w">
        <div class="mini-bar"><span class="${effClass(m.eff)}" style="width:${m.eff}%"></span></div>
        <em>${m.eff}%</em>
      </td>
      <td class="m-out">${fmt(m.output)} m</td>
      <td class="m-stop">${m.stops}x</td>
      <td class="m-down">${m.downtime} mnt</td>
    </tr>`;
}

function renderAnalysis() {
  const box = $("analysis");
  const list = visibleLines();
  setText($("anaCount"),
    `${list.length} line • ${list.length * (SUMMARY.mesin_per_line || 10)} mesin`);

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
    <div class="ana${open ? " open" : ""}" data-line="${l.id}">
      <div class="ana-head">
        <span class="caret">&#9654;</span>
        <span class="ana-name"><i class="s-${l.status}"></i>${l.name}</span>
        <span class="pill ${l.status}">${l.status_text}</span>
        <span class="ana-alert"${l.alert ? "" : " hidden"}>&#9888; ${l.alert ? l.alert.label : ""}</span>
        <span class="mo-dots">${l.mo_groups.map((g, i) =>
          `<b class="mo-c${i % 5}" title="${g.mo} — mesin ${g.machines.join(", ")}">${g.mo.replace("MO-", "")}</b>`
        ).join("")}</span>
        <div class="ana-stats">
          <div>Operator<b class="a-op">${l.operator}</b></div>
          <div>Mesin Jalan<b class="a-run">${l.mesin_run}/${l.mesin}</b></div>
          <div>RPM<b class="a-rpm">${l.rpm || "-"}</b></div>
          <div>Efisiensi<b class="a-eff">${l.eff}%</b></div>
          <div>Output<b class="a-out">${fmt(l.output)} m</b></div>
          <div>Stop<b class="a-stop">${l.stops}x</b></div>
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
  else if (view === "analysis") patchAnalysis();
}

function patchTree() {
  ALL.forEach(l => {
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

  $("anaExpand").onclick   = () => { visibleLines().forEach(l => openAna.add(l.id)); render(); };
  $("anaCollapse").onclick = () => { openAna.clear(); render(); };

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
    if (typeof CAL !== "undefined" && CAL.on) { calClose(); return; }
    detailLine = null; setView("camera");
  });
}

/* ---------------- REFRESH SNAPSHOT (mode img) ---------------- */
setInterval(() => {
  if (STREAM_MODE !== "img" || !IMG_REFRESH) return;
  const stamp = Date.now();
  document.querySelectorAll(".cam img[data-refresh]").forEach(img => {
    const base = img.dataset.refresh;
    img.src = base + (base.includes("?") ? "&" : "?") + "_t=" + stamp;
  });
}, Math.max(1, IMG_REFRESH) * 1000);

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
  bindEvents();
  try {
    const res = await fetch("/api/lines");
    applySnapshot(await res.json());
  } catch (err) {
    setHTML($("grid"), `<div class="empty">Gagal mengambil data dari server.</div>`);
  }
  connect();
}

boot();
