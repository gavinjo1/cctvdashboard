/* Pengganti prompt()/confirm()/alert() bawaan.
   Dipisah karena tidak menyentuh data dashboard sama sekali: masuknya
   judul dan pilihan, keluarnya Promise berisi pilihan pengguna. */
import { $, esc, setHTML, setText } from "./inti.js";

/* ---------------- DIALOG ----------------
   Pengganti prompt()/confirm()/alert() bawaan. Mengembalikan Promise:
   nilai pilihan, atau null bila dibatalkan. */
let dlgTutup = null;              // penutup dialog yang sedang terbuka

export function dlgOpen({ title, body, actions, onMount }) {
  if (dlgTutup) dlgTutup(null);   // hanya satu dialog pada satu waktu
  const back = $("dlgBack");
  const fokusSebelumnya = document.activeElement;

  setText($("dlgTitle"), title);
  setHTML($("dlgBody"), body || "");
  setHTML($("dlgActions"), (actions || []).map((a, i) =>
    `<button class="dlg-btn ${esc(a.kind || "ghost")}" data-i="${i}">${
      esc(a.label)}</button>`).join(""));

  return new Promise(resolve => {
    const selesai = val => {
      if (dlgTutup !== selesai) return;
      dlgTutup = null;
      back.hidden = true;
      document.removeEventListener("keydown", onKey, true);
      if (fokusSebelumnya && fokusSebelumnya.focus) fokusSebelumnya.focus();
      resolve(val);
    };
    dlgTutup = selesai;

    function onKey(e) {
      if (e.key === "Escape") { e.stopPropagation(); selesai(null); }
    }
    document.addEventListener("keydown", onKey, true);

    back.hidden = false;
    back.onclick = e => { if (e.target === back) selesai(null); };
    $("dlgActions").querySelectorAll("[data-i]").forEach(b => {
      b.onclick = () => {
        const act = actions[+b.dataset.i];
        selesai(act.value !== undefined ? act.value : act.label);
      };
    });
    if (onMount) onMount($("dlgBody"), selesai);
    const fokus = $("dlg").querySelector("input, .dlg-btn.primary, .dlg-btn");
    if (fokus) fokus.focus();
  });
}

export const dlgInfo = (title, msg) => dlgOpen({
  title, body: `<p class="dlg-msg">${esc(msg)}</p>`,
  actions: [{ label: "Tutup", kind: "primary", value: true }],
});

export const dlgConfirm = (title, msg, ya = "Lanjutkan") => dlgOpen({
  title, body: `<p class="dlg-msg">${esc(msg)}</p>`,
  actions: [{ label: "Batal", value: null },
            { label: ya, kind: "danger", value: true }],
});
