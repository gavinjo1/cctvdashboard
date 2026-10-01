/* Siapa yang menutup alert — inti jejak audit.
   Nama disimpan BESERTA periode shiftnya, jadi di PC bersama nama
   shift 1 tidak menempel sampai shift 2. State-nya (nama + daftar
   nama yang sah) milik modul ini sendiri. */
import { $, esc, setHTML, setText } from "./inti.js";
import { dlgInfo, dlgOpen } from "./dialog.js";
import { S } from "./keadaan.js";

/* Nama yang sedang dipakai. Milik modul ini; daftar nama yang SAH
   datang dari server lewat S.OPERATOR_LIST. */
let OPERATOR = "";
export const operatorNama = () => OPERATOR;

/* ---------------- IDENTITAS OPERATOR ----------------
   Alert hanya boleh ditutup dengan nama operator — supaya ada bukti siapa
   yang menangani. Nama disimpan BESERTA periode shiftnya: di PC bersama,
   nama shift 1 kalau tidak pernah kedaluwarsa akan menempel pada
   penutupan alert shift 3, dan jejak auditnya jadi salah dengan percaya
   diri — lebih buruk daripada tidak ada catatan sama sekali. */
export function operatorSimpan(nama) {
  OPERATOR = nama;
  try {
    localStorage.setItem("cctv_operator",
      JSON.stringify({ nama, periode: S.SHIFT.period_key || "" }));
  } catch (e) { /* localStorage diblokir: cukup simpan di memori */ }
}

export function operatorLupakan() {
  OPERATOR = "";
  try { localStorage.removeItem("cctv_operator"); } catch (e) {}
}

/* Panggil tiap snapshot: buang nama begitu shift berganti. */
export function operatorPeriksaPeriode() {
  const kunci = S.SHIFT.period_key;
  if (!kunci) return;
  let simpanan = null;
  try { simpanan = JSON.parse(localStorage.getItem("cctv_operator") || "null"); }
  catch (e) { simpanan = null; }

  if (!simpanan || !simpanan.nama) { OPERATOR = ""; return; }
  if (simpanan.periode !== kunci) {
    operatorLupakan();                    // shift sudah berganti
    return;
  }
  OPERATOR = simpanan.nama;
}

/* Dialog pemilih nama. Dengan daftar operator: cari lalu pilih.
   Tanpa daftar: ketik bebas (tetap dicatat di jejak audit). */
export function askOperator() {
  const punyaDaftar = S.OPERATOR_LIST.length > 0;
  const body = `
    <p class="dlg-msg">Nama Anda dicatat sebagai penanggung jawab penutupan
      alert ini.</p>
    <input class="dlg-input" id="dlgOpInput" type="text" autocomplete="off"
           placeholder="${punyaDaftar ? "Cari nama..." : "Ketik nama Anda"}"
           value="${esc(OPERATOR)}">
    ${punyaDaftar ? `<div class="dlg-list" id="dlgOpList"></div>` : ""}
    <p class="dlg-note" id="dlgOpNote">${punyaDaftar
      ? `${S.OPERATOR_LIST.length} nama terdaftar`
      : "Belum ada daftar operator — nama bebas, tetap dicatat."}</p>`;

  return dlgOpen({
    title: "Siapa yang menangani?",
    body,
    // TOMBOL SIMPAN WAJIB ADA. Dulu di sini hanya ada "Batal", dan satu-
    // satunya cara mengirim nama adalah menekan Enter — tanpa petunjuk apa
    // pun di layar. Tanpa daftar operator (data/operators.json tidak wajib
    // ada), orang mengetik namanya lalu mentok: alert tidak bisa ditutup,
    // dan tidak ada yang salah kelihatan di layar.
    actions: [{ label: "Batal", value: null },
              { label: "Simpan", kind: "primary", value: null }],
    onMount: (box, selesai) => {
      const input = box.querySelector("#dlgOpInput");
      const list = box.querySelector("#dlgOpList");

      const pilih = nama => {
        const bersih = (nama || "").trim();
        if (!bersih) { setText(box.querySelector("#dlgOpNote"),
                               "Nama tidak boleh kosong."); return; }
        if (punyaDaftar && !S.OPERATOR_LIST.includes(bersih)) {
          setText(box.querySelector("#dlgOpNote"),
                  "Pilih salah satu nama dari daftar.");
          return;
        }
        operatorSimpan(bersih);
        selesai(bersih);
      };

      const gambar = () => {
        if (!list) return;
        const q = input.value.trim().toLowerCase();
        const cocok = S.OPERATOR_LIST.filter(n => n.toLowerCase().includes(q));
        setHTML(list, cocok.length
          ? cocok.map(n => `<button class="dlg-op" data-n="${esc(n)}">${
              esc(n)}</button>`).join("")
          : `<span class="dlg-kosong">Tidak ada nama yang cocok.</span>`);
        list.querySelectorAll("[data-n]").forEach(b => {
          b.onclick = () => pilih(b.dataset.n);
        });
      };

      input.oninput = gambar;
      input.onkeydown = e => {
        if (e.key !== "Enter") return;
        e.preventDefault();
        if (!punyaDaftar) return pilih(input.value);
        const q = input.value.trim().toLowerCase();
        const cocok = S.OPERATOR_LIST.filter(n => n.toLowerCase().includes(q));
        if (cocek1(cocok, input.value)) return;
        // Tidak ada yang cocok, atau masih banyak pilihan. Wajib berkata
        // sesuatu: Enter yang tidak menghasilkan apa-apa membuat operator
        // menekannya berulang kali sambil mengira dashboard menggantung.
        setText(box.querySelector("#dlgOpNote"), cocok.length
          ? `${cocok.length} nama cocok — pilih salah satu dari daftar.`
          : "Nama itu tidak ada di daftar operator.");
      };

      // pilih bila pilihannya sudah tidak ambigu
      function cocek1(cocok, nilai) {
        if (cocok.length === 1) { pilih(cocok[0]); return true; }
        if (cocok.includes(nilai.trim())) { pilih(nilai); return true; }
        return false;
      }
      gambar();

      // Tombol "Simpan" dicegat supaya melewati pemeriksaan yang SAMA dengan
      // Enter — nama kosong atau di luar daftar tetap ditolak, bukan lolos
      // hanya karena dikirim lewat tombol.
      //
      // Tombolnya hidup di #dlgActions, bukan di dalam `box` (onMount hanya
      // menerima badan dialog), jadi dicari dari sana.
      const tombolSimpan = document.querySelector('#dlgActions [data-i="1"]');
      if (tombolSimpan) {
        tombolSimpan.onclick = () => {
          if (!punyaDaftar) return pilih(input.value);
          const q = input.value.trim().toLowerCase();
          const cocok = S.OPERATOR_LIST.filter(n => n.toLowerCase().includes(q));
          if (cocek1(cocok, input.value)) return;
          setText(box.querySelector("#dlgOpNote"), cocok.length
            ? `${cocok.length} nama cocok — pilih salah satu dari daftar.`
            : "Nama itu tidak ada di daftar operator.");
        };
      }
      input.focus();
    },
  });
}

export async function resolveAlert(alertId, action) {
  const by = OPERATOR || await askOperator();
  if (!by) return;
  try {
    const r = await fetch(`/api/alerts/${alertId}/resolve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, by }),
    });
    if (r.status === 400) {
      operatorLupakan();
      const d = await r.json().catch(() => ({}));
      await dlgInfo("Nama tidak dikenal",
                    d.detail || "Nama operator tidak ada di daftar.");
    }
  } catch (e) { /* snapshot berikutnya akan menyusul */ }
}
