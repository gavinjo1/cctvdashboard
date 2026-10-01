/* Pembantu kecil yang dipakai di mana-mana: keluaran aman untuk HTML,
   pembaruan DOM yang tidak menyentuh elemen bila nilainya tidak berubah,
   dan penamaan status/warna mesin.

   Tidak ada state di sini, tidak ada impor dari modul lain. Itu sengaja:
   berkas ini boleh diimpor siapa saja tanpa risiko impor melingkar. */

export const $ = id => document.getElementById(id);
export const fmt = n => (n || 0).toLocaleString("id-ID");
export const jam = d => d
  ? new Date(d).toLocaleString("id-ID", { hour12: false }) : "—";
export const durasi = s => {
  const j = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return j ? `${j}j ${m}m` : `${m}m`;
};

export const statusText = s => ({ run: "Running", idle: "Idle", stop: "Stop",
                           off: "Offline", unknown: "Belum terbaca" }[s] || s);

/* Kelas warna satu mesin. WARNA LAMPU MENANG atas status, supaya yang di
   layar sama dengan yang terlihat di menara mesin. Mesin yang lampunya
   belum dikalibrasi tidak boleh ikut berwarna — "belum terbaca" harus
   kelihatan beda dari "sehat". */
export const LAMPU_SAH = ["merah", "oranye", "kuning", "hijau", "biru",
                   "ungu", "putih"];
export function unitClass(m) {
  if (m.status === "unknown") return "u-unknown";
  if (m.color && LAMPU_SAH.includes(m.color)) return "u-lamp-" + m.color;
  // Menara padam = mesin jalan -> ABU, bukan hijau. Di pabrik ini hijau
  // berarti PAKAN PUTUS, jadi memakai warna "run" membuat mesin sehat dan
  // mesin bermasalah tampil dengan warna yang sama persis.
  if (m.vision && m.status === "run") return "u-padam";
  return "u-" + m.status;
}
export function unitTitle(m) {
  const asal = m.vision ? (m.color ? "lampu " + m.color : "menara padam")
                        : "belum ada kotak lampu";
  return m.name + " — " + statusText(m.status) + " (" + asal + ")";
}
export const effClass = e => e >= 80 ? "good" : e >= 60 ? "mid" : "bad";

/* Amankan teks sebelum disisipkan ke innerHTML.
 *
 * Isi alert (label, aktivitas, zona) datang dari POST /api/alerts — siapa pun
 * yang bisa menjangkau dashboard di jaringan pabrik bisa mengisinya. Tanpa
 * penyaringan ini, teks yang dikirim ke sana dijalankan sebagai kode di
 * layar SETIAP operator, dan bertahan sampai alert ditutup.
 *
 * Nama line, operator, dan kode MO ikut disaring: sumbernya master data
 * pabrik, bukan sesuatu yang dikarang dashboard ini.
 */
export function esc(v) {
  if (v === null || v === undefined) return "";
  return String(v)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/* Angka yang ikut masuk ke atribut style (lebar bar, posisi denah).
   esc() tidak cukup di sana: "100%;background:url(...)" tetap sah sebagai
   CSS meski tidak mengandung tanda kurung sudut. */
export function num(v, fallback = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? n : fallback;
}

/* tulis hanya kalau berubah — mencegah repaint & kedip */
export function setText(el, val) {
  if (el && el.textContent !== String(val)) el.textContent = val;
}
export function setHTML(el, val) {
  if (el && el.innerHTML !== val) el.innerHTML = val;
}
export function setClass(el, cls) {
  if (el && el.className !== cls) el.className = cls;
}
export function setWidth(el, pct) {
  const v = pct + "%";
  if (el && el.style.width !== v) el.style.width = v;
}
