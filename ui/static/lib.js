// Pure helpers shared by the sheets and takeoff pages (no network calls here; those live in api.js).
window.KIND_LABEL = {
  partition_types: "Partition types", floor_plan: "Floor plan", reflected_ceiling: "RCP",
  elevations: "Elevations", details: "Details", schedules: "Schedules", other: "Other",
};

const DISCIPLINES = {
  G: "General", A: "Architectural", I: "Interiors", S: "Structural", M: "Mechanical", E: "Electrical",
  P: "Plumbing", F: "Fire protection", C: "Civil", L: "Landscape", T: "Telecom", D: "Demolition",
};
const DISC_ORDER = Object.keys(DISCIPLINES);

// A-101 -> {code: "A", name: "Architectural"}. Sheets without a number land in "Other".
window.discipline = (sheetNo) => {
  const m = /^\s*([A-Za-z])/.exec(sheetNo || "");
  const code = m ? m[1].toUpperCase() : "·";
  return { code, name: DISCIPLINES[code] || (m ? code : "Other") };
};
window.discRank = (code) => {
  const i = DISC_ORDER.indexOf(code);
  return i < 0 ? 100 + (code === "·" ? 50 : code.charCodeAt(0)) : i;
};

// Group sheets (already in page order) by discipline: [{code, name, sheets: [...]}], disciplines in plan-set order.
window.groupSheets = (sheets) => {
  const by = new Map();
  for (const s of sheets) {
    const d = discipline(s.sheet_no);
    if (!by.has(d.code)) by.set(d.code, { ...d, sheets: [] });
    by.get(d.code).sheets.push(s);
  }
  return [...by.values()].sort((a, b) => discRank(a.code) - discRank(b.code));
};

window.scaleText = (scale) => (scale ? (scale.pt_per_ft ? scale.label : scale.label || "NTS") : "no scale");

window.scaleNote = (s) => {
  if (!s) return "no scale found";
  if (s.source === "manual") return /^calibrated/.test(s.label || "") ? "calibrated by you" : "set by you";
  const src = { title_block: "title block", view_title: "view titles", bookmark: "bookmark" }[s.source] || s.source;
  return `auto · ${src}` + (s.candidates?.length > 1 ? ` · also ${s.candidates.filter((c) => c !== s.label).join(", ")}` : "");
};

// 48.25 ft -> 48' 3"
window.fmtFtIn = (ft) => {
  if (!isFinite(ft)) return "—";
  const total = Math.round(Math.abs(ft) * 12);
  return `${ft < 0 ? "-" : ""}${Math.floor(total / 12)}' ${total % 12}"`;
};
window.fmtNum = (v, d = 1) => Number(v || 0).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

// "24'-6 1/2\"", "24' 6\"", "24.5", "300\"", "7.5m", "7500mm" -> feet (or null)
window.parseLength = (text) => {
  let t = String(text || "").trim().toLowerCase().replace(/[‘’′]/g, "'").replace(/[“”″]/g, '"').replace(/\s+/g, " ");
  if (!t) return null;
  const frac = (s) => {
    const m = /^(?:(\d+)[- ])?(\d+)\/(\d+)$/.exec(s.trim());
    if (m) return (m[1] ? +m[1] : 0) + +m[2] / +m[3];
    return /^\d+(\.\d+)?$/.test(s.trim()) ? +s : NaN;
  };
  let m;
  if ((m = /^(\d+(?:\.\d+)?)\s*mm$/.exec(t))) return +m[1] / 304.8;
  if ((m = /^(\d+(?:\.\d+)?)\s*m$/.exec(t))) return +m[1] / 0.3048;
  if ((m = /^(\d+(?:\.\d+)?)\s*(?:in|")$/.exec(t))) return +m[1] / 12;
  if ((m = /^(\d+(?:\.\d+)?)\s*(?:ft|')\s*-?\s*(.*)$/.exec(t))) {
    const rest = m[2].replace(/\s*(?:in|")\s*$/, "").trim();
    const inch = rest ? frac(rest) : 0;
    return isNaN(inch) ? null : +m[1] + inch / 12;
  }
  return /^\d+(\.\d+)?$/.test(t) ? +t : null;
};
