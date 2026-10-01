/* Keadaan bersama — satu-satunya data yang dilihat lebih dari satu modul.
   Dibungkus objek `S` supaya modul lain bisa MEMBACA nilai terbaru: modul
   ES mengekspor ikatan, bukan salinan, tetapi hanya pemiliknya yang boleh
   menugaskan ulang. Dengan objek, app.js cukup menulis S.ALL = ... dan
   semua modul langsung melihatnya.

   Yang TIDAK di sini: state yang cuma dipakai satu modul (pilihan tema,
   nama operator, periode laporan). Itu tinggal di modulnya masing-masing —
   state bersama yang tidak perlu adalah cara tercepat membuat dua bagian
   saling merusak. */
export const S = {
  GROUPS: [], ALL: [], HALLS: [], SUMMARY: {}, SHIFT: {},
  STREAM_MODE: "video", IMG_REFRESH: 2,
  SOURCE_MODE: "sim", VISION_LEASE: 60,
};

/** Cari satu line dari snapshot terakhir. */
export const byId = id => S.ALL.find(l => l.id === id);
