/* Laporan shift: pemilih periode, pengambilan, dan penggambarannya.
   Nilai sesaat (efisiensi, RPM) datang sudah dirata-rata terbobot
   waktu dari server — bukan cuplikan terakhir shift.
   repPeriods/repData/repKey/anaTab cuma dipakai di sini. */
import { $, durasi, effClass, esc, fmt, jam, num, setHTML, setText } from "./inti.js";

/* State laporan — hanya dipakai di berkas ini. */
let repPeriods = [], repData = null, repKey = null;
let anaTab = "live";                 // live | report

/** Tab analisa yang sedang aktif; dibaca app.js saat menggambar ulang. */
export const anaTabAktif = () => anaTab;

/* gambarUlang() milik app.js. Disuntikkan saat boot supaya laporan tidak perlu
   mengimpor app.js — kalau saling impor, keduanya gagal dimuat. */
let gambarUlang = () => {};
export function laporanHubungkan(fn) { gambarUlang = fn; }

/* ================================================================
   LAPORAN SHIFT
   Nilai sesaat (efisiensi, RPM) datang sudah dirata-rata terbobot
   waktu dari server — bukan cuplikan terakhir shift.
   ================================================================ */

export async function loadPeriods() {
  try {
    repPeriods = await fetch("/api/report/periods").then(r => r.json());
  } catch (e) { repPeriods = []; }
  const sel = $("repPeriod");
  sel.innerHTML = repPeriods.map(p => {
    const tgl = p.started_at
      ? new Date(p.started_at).toLocaleDateString("id-ID",
          { day: "2-digit", month: "short" })
      : "";
    return `<option value="${esc(p.period_key)}">${esc(tgl)} · ${esc(p.shift)}${
      p.live ? " (berjalan)" : ""}</option>`;
  }).join("");
  if (!repKey && repPeriods.length) repKey = repPeriods[0].period_key;
  sel.value = repKey || "";
}

export async function loadReport() {
  const box = $("report");
  box.innerHTML = `<div class="empty">Menyusun laporan…</div>`;
  try {
    const q = repKey ? `?period_key=${encodeURIComponent(repKey)}` : "";
    const r = await fetch("/api/report/shift" + q);
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      box.innerHTML = `<div class="empty">${esc(d.detail || "Laporan tidak tersedia.")}</div>`;
      return;
    }
    repData = await r.json();
    renderReport();
  } catch (e) {
    box.innerHTML = `<div class="empty">Gagal mengambil laporan.</div>`;
  }
}

export function renderReport() {
  const d = repData;
  if (!d) return;
  const r = d.ringkasan, a = d.alerts || {};

  const tile = (label, value, sub) => `
    <div class="rep-tile">
      <div class="rep-label">${label}</div>
      <div class="rep-value">${value}</div>
      <div class="rep-sub">${sub || "&nbsp;"}</div>
    </div>`;

  const lineRow = l => `
    <tr>
      <td class="m-name">${esc(l.name)}</td>
      <td>${esc(l.operator || "—")}</td>
      <td class="w">
        <div class="mini-bar"><span class="${effClass(l.eff_avg)}" style="width:${num(l.eff_avg)}%"></span></div>
        <em>${l.eff_avg}%</em>
      </td>
      <td class="tnum">${l.eff_run}%</td>
      <td class="tnum">${l.availability}%</td>
      <td class="tnum">${fmt(l.output)} m</td>
      <td class="tnum">${l.stops}x</td>
      <td class="tnum">${durasi(l.sec_stop + l.sec_idle)}</td>
    </tr>`;

  const machRow = m => `
    <tr>
      <td class="m-name">${esc(m.name)}</td>
      <td class="mono-cell">${esc(m.line_id)}</td>
      <td class="w">
        <div class="mini-bar"><span class="${effClass(m.eff_avg)}" style="width:${num(m.eff_avg)}%"></span></div>
        <em>${m.eff_avg}%</em>
      </td>
      <td class="tnum">${m.availability}%</td>
      <td class="tnum">${fmt(m.output)} m</td>
      <td class="tnum">${m.stops}x</td>
      <td class="tnum">${durasi(m.sec_stop)}</td>
    </tr>`;

  $("report").innerHTML = `
    <div class="rep-head">
      <div>
        <h3 class="rep-title">Laporan ${esc(d.shift)}${d.live ? " — sedang berjalan" : ""}</h3>
        <p class="rep-meta">${jam(d.started_at)} &nbsp;→&nbsp; ${d.live ? "sekarang" : jam(d.ended_at)}
          &nbsp;·&nbsp; ${r.line} line, ${r.mesin} mesin
          &nbsp;·&nbsp; terpantau ${r.durasi_menit} dari ${r.durasi_shift_menit} menit</p>
      </div>
      <div class="rep-flags">
        ${d.live ? '<span class="rep-badge">Angka belum final</span>' : ""}
        ${r.cakupan < 95 ? `<span class="rep-badge warn">Cakupan data ${r.cakupan}%</span>` : ""}
      </div>
    </div>
    ${r.cakupan < 95 ? `<div class="rep-warn">
      Dashboard hanya merekam <b>${r.durasi_menit} menit</b> dari ${r.durasi_shift_menit}
      menit shift ini, sehingga angka di bawah mewakili ${r.cakupan}% waktu shift.
      Penyebab umum: dashboard baru dinyalakan di tengah shift, atau sempat mati.
      Output dan jumlah stop tetap benar karena bersifat akumulatif; efisiensi dan
      availability hanya menggambarkan periode yang terekam.
    </div>` : ""}

    <div class="rep-tiles">
      ${tile("Output", `${fmt(r.output)}<small> m</small>`, "seluruh line")}
      ${tile("Efisiensi Rata-rata", `${r.eff_avg}%`, "dibobot waktu")}
      ${tile("Availability", `${r.availability}%`, "porsi waktu beroperasi")}
      ${tile("Total Stop", `${fmt(r.stops)}<small>x</small>`, "seluruh mesin")}
      ${tile("Alert", `${a.total ?? 0}`, `${a.belum_ditangani ?? 0} belum ditangani`)}
      ${tile("Waktu Tanggap", a.avg_response_sec != null ? durasi(a.avg_response_sec) : "—", "rata-rata")}
    </div>

    <h4 class="rep-h">Ringkasan per Line</h4>
    <div class="rep-table">
      <table class="ana-table">
        <thead><tr>
          <th>Line</th><th>Operator</th><th class="w">Efisiensi</th>
          <th>Saat Jalan</th><th>Availability</th><th>Output</th><th>Stop</th><th>Tidak Jalan</th>
        </tr></thead>
        <tbody>${d.lines.map(lineRow).join("")}</tbody>
      </table>
    </div>

    <h4 class="rep-h">Sepuluh Mesin dengan Efisiensi Terendah</h4>
    <div class="rep-table">
      <table class="ana-table">
        <thead><tr>
          <th>Mesin</th><th>Line</th><th class="w">Efisiensi</th>
          <th>Availability</th><th>Output</th><th>Stop</th><th>Waktu Stop</th>
        </tr></thead>
        <tbody>${d.mesin_terburuk.map(machRow).join("")}</tbody>
      </table>
    </div>

    <h4 class="rep-h">Sepuluh Mesin Paling Sering Berhenti</h4>
    <div class="rep-table">
      <table class="ana-table">
        <thead><tr>
          <th>Mesin</th><th>Line</th><th class="w">Efisiensi</th>
          <th>Availability</th><th>Output</th><th>Stop</th><th>Waktu Stop</th>
        </tr></thead>
        <tbody>${d.mesin_paling_sering_stop.map(machRow).join("")}</tbody>
      </table>
    </div>

    ${(a.by_label || []).length ? `
    <h4 class="rep-h">Alert menurut Jenis</h4>
    <div class="rep-table">
      <table class="ana-table">
        <thead><tr><th>Jenis</th><th>Jumlah</th></tr></thead>
        <tbody>${a.by_label.map(x =>
          `<tr><td>${esc(x.label)}</td><td class="tnum">${num(x.c)}x</td></tr>`).join("")}</tbody>
      </table>
    </div>` : ""}

    <p class="rep-note">Efisiensi dan RPM dirata-rata dengan pembobotan waktu sepanjang shift.
      Kolom <b>Efisiensi</b> menghitung mesin berhenti sebagai 0%, sedangkan
      <b>Saat Jalan</b> hanya menghitung waktu mesin beroperasi. Output dan jumlah
      stop adalah nilai akumulatif sejak awal shift.</p>`;
}

export function setAnaTab(tab) {
  anaTab = tab;
  $("tabLive").classList.toggle("on", tab === "live");
  $("tabReport").classList.toggle("on", tab === "report");
  $("liveTools").hidden = tab !== "live";
  $("repTools").hidden = tab !== "report";
  $("analysis").hidden = tab !== "live";
  $("report").hidden = tab !== "report";
  setText($("anaTitle"), tab === "live" ? "Analisa per Mesin" : "Laporan Shift");
  $("anaCount").hidden = tab !== "live";
  if (tab === "report") { loadPeriods().then(loadReport); }
  else gambarUlang();
}
