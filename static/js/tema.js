/* Pilihan tampilan: terang, gelap, ikuti sistem.
   Seluruh state-nya milik sendiri (pilihan + pendengar media query),
   jadi tidak ada yang perlu dibagi dengan modul lain. */
import { $ } from "./inti.js";
import { dlgOpen } from "./dialog.js";

/* ---------------- TEMA ----------------
   Tiga pilihan: terang, gelap, ikuti sistem.

   "sistem" diselesaikan di sini, bukan lewat @media di CSS. Dengan cara
   itu CSS cukup punya SATU blok tema gelap ([data-theme="dark"]); kalau
   diselesaikan di CSS, seluruh daftar token harus digandakan di dalam
   media query, dan tiap warna baru nanti harus ditambahkan di dua
   tempat — yang cepat atau lambat akan terlewat.

   Nilai tersimpan di peramban masing-masing: PC ruang kendali boleh
   gelap sementara PC lantai produksi tetap terang. */
export const TEMA = ["terang", "gelap", "sistem"];
export const TEMA_LABEL = { terang: "Terang", gelap: "Gelap", sistem: "Ikuti sistem" };
let temaPilihan = "terang";
let temaMedia = null;
let temaLepasPendengar = null;

export function temaGelapAktif(pilihan) {
  return pilihan === "gelap" || (pilihan === "sistem" &&
    window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches);
}

export function temaTerapkan(pilihan, simpan = true) {
  if (!TEMA.includes(pilihan)) pilihan = "terang";
  temaPilihan = pilihan;
  const gelap = temaGelapAktif(pilihan);

  if (gelap) document.documentElement.dataset.theme = "dark";
  else delete document.documentElement.dataset.theme;

  // warna bilah alamat peramban di layar sentuh / mode kios
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = gelap ? "#0d1117" : "#ffffff";

  if (simpan) {
    try { localStorage.setItem("cctv_theme", pilihan); } catch (e) {}
  }

  // Hanya dalam mode "sistem" dashboard ikut berubah saat OS berganti tema.
  temaPantauSistem(pilihan === "sistem");
}

export function temaPantauSistem(aktif) {
  if (temaLepasPendengar) { temaLepasPendengar(); temaLepasPendengar = null; }
  if (!aktif || !window.matchMedia) return;

  if (!temaMedia) temaMedia = matchMedia("(prefers-color-scheme: dark)");
  const ikut = () => { if (temaPilihan === "sistem") temaTerapkan("sistem", false); };
  const lepas = [];

  if (temaMedia.addEventListener) {
    temaMedia.addEventListener("change", ikut);
    lepas.push(() => temaMedia.removeEventListener("change", ikut));
  } else if (temaMedia.addListener) {          // peramban lama
    temaMedia.addListener(ikut);
    lepas.push(() => temaMedia.removeListener(ikut));
  }

  // Jaring pengaman. Peristiwa "change" tidak selalu sampai — terutama bila
  // tema OS berganti saat tab sedang tidak terlihat. Dashboard ini menyala
  // berhari-hari tanpa disentuh, jadi sekali terlewat berarti tema salah
  // sampai ada yang memuat ulang halaman. Periksa ulang setiap kali halaman
  // kembali terlihat: murah, dan menutup celah itu.
  document.addEventListener("visibilitychange", ikut);
  lepas.push(() => document.removeEventListener("visibilitychange", ikut));

  temaLepasPendengar = () => lepas.forEach(f => f());
}

export function temaMuat() {
  let p = "terang";
  try { p = localStorage.getItem("cctv_theme") || "terang"; } catch (e) {}
  temaTerapkan(p, false);
}

/* ---------------- PENGATURAN ---------------- */
export function bukaPengaturan() {
  const baris = TEMA.map(t => `
    <button class="set-opt${t === temaPilihan ? " on" : ""}" data-tema="${t}">
      <span class="set-swatch ${t}"></span>
      <span class="set-opt-txt">${TEMA_LABEL[t]}</span>
      <span class="set-check">&#10003;</span>
    </button>`).join("");

  return dlgOpen({
    title: "Pengaturan",
    body: `
      <div class="set-grup">
        <div class="set-label">Tampilan</div>
        <div class="set-opts" id="setTema">${baris}</div>
        <p class="dlg-note">Pilihan ini hanya berlaku di peramban dan komputer
          ini — layar lain tidak ikut berubah.</p>
      </div>`,
    actions: [{ label: "Tutup", kind: "primary", value: true }],
    onMount: box => {
      box.querySelectorAll("[data-tema]").forEach(b => {
        b.onclick = () => {
          temaTerapkan(b.dataset.tema);
          box.querySelectorAll("[data-tema]").forEach(x =>
            x.classList.toggle("on", x.dataset.tema === temaPilihan));
        };
      });
    },
  });
}
