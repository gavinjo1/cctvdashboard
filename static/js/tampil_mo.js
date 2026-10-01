/* Portal MO: PPIC mengisi nomor order per mesin.
   Hanya MEMBACA daftar line dari keadaan bersama; seluruh sisanya
   DOM dan satu permintaan simpan per line. */
import { $, esc, num, setHTML, setText } from "./inti.js";
import { dlgInfo } from "./dialog.js";
import { S } from "./keadaan.js";

/* ---------------- PORTAL MO ----------------
   Nomor MO dulu dikarang acak tiap dashboard menyala. Di sini PPIC
   mengisinya sendiri dan isiannya bertahan.

   Disimpan PER LINE, bukan per ketikan: mengirim tiap huruf ke server akan
   membanjiri jaringan dan membuat setengah line tersimpan setengah jalan
   kalau koneksi putus di tengah. Tombol "Simpan line" mengirim keadaan
   LENGKAP satu line sekaligus. */
export function renderMoPortal() {
  const wrap = $("moPortal");
  const lines = S.ALL;
  setText($("moPortalCount"), `${lines.length} line`);

  setHTML(wrap, lines.map(l => `
    <div class="mo-line" data-line="${esc(l.id)}">
      <div class="mo-line-head">
        <b>${esc(l.name)}</b>
        <span class="mo-line-area">${esc(l.area)}</span>
        <span class="mo-line-sisa" data-sisa></span>
        <span class="mo-line-aksi">
          <button class="btn mo-isi-semua" type="button">Isi semua</button>
          <button class="btn primary mo-simpan" type="button">Simpan line</button>
        </span>
      </div>
      <div class="mo-grid">
        ${l.machines.map(m => `
          <label class="mo-sel">
            <span class="mo-no">${String(m.no).padStart(2, "0")}</span>
            <input class="mo-input" type="text" maxlength="32"
                   data-m="${num(m.no)}" placeholder="—"
                   value="${esc(m.order_mo || "")}">
          </label>`).join("")}
      </div>
    </div>`).join(""));

  wrap.querySelectorAll(".mo-line").forEach(box => {
    const lineId = box.dataset.line;
    const inputs = [...box.querySelectorAll(".mo-input")];

    const hitungSisa = () => {
      const kosong = inputs.filter(i => !i.value.trim()).length;
      const el = box.querySelector("[data-sisa]");
      setText(el, kosong ? `${kosong} mesin belum ditugasi` : "semua ditugasi");
      el.classList.toggle("ok", !kosong);
    };
    inputs.forEach(i => { i.oninput = hitungSisa; });
    hitungSisa();

    // "Isi semua" menyalin isian PERTAMA yang tidak kosong ke seluruh mesin.
    // Satu line biasanya mengerjakan satu order; mengetik nomor yang sama
    // sepuluh kali adalah cara paling gampang salah ketik satu di antaranya.
    box.querySelector(".mo-isi-semua").onclick = () => {
      const sumber = inputs.find(i => i.value.trim());
      if (!sumber) {
        dlgInfo("Isi semua", "Ketik dulu satu nomor MO di salah satu mesin.");
        return;
      }
      inputs.forEach(i => { i.value = sumber.value.trim(); });
      hitungSisa();
    };

    box.querySelector(".mo-simpan").onclick = async btn => {
      const tombol = box.querySelector(".mo-simpan");
      const mesin = {};
      inputs.forEach(i => { mesin[i.dataset.m] = i.value.trim(); });
      tombol.disabled = true;
      const labelAsli = tombol.textContent;
      setText(tombol, "Menyimpan...");
      try {
        const r = await fetch(`/api/lines/${encodeURIComponent(lineId)}/mo`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ machines: mesin }),
        });
        if (!r.ok) {
          const e = await r.json().catch(() => ({}));
          throw new Error(e.detail || `HTTP ${r.status}`);
        }
        setText(tombol, "Tersimpan");
        setTimeout(() => { setText(tombol, labelAsli); tombol.disabled = false; }, 1200);
      } catch (e) {
        tombol.disabled = false;
        setText(tombol, labelAsli);
        dlgInfo("Gagal menyimpan MO", String(e.message || e));
      }
    };
  });
}
