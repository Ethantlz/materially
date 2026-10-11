// Takeoff workspace.
//
// Coordinates: measurements are stored and sent as PDF points (72 per paper inch, origin top-left). The canvas works in
// image px (pt * ips, ips = image px per pt), and one CSS transform (translate + scale) maps image px to the screen:
//   screen = image px * k + (tx, ty)      pt = (screen - (tx, ty)) / k / ips
// The sheet <img> and the SVG overlay live in the same transformed .stage, so they always line up.
// Every network call goes through static/api.js (Takeoff.*).
(() => {
"use strict";
const B = window.TAKEOFF;
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const NS = "http://www.w3.org/2000/svg";
const UNIT = { linear: "lf", area: "sf", count: "ea" };
const KIND_NAME = { linear: "Linear", area: "Area", count: "Count" };
const MIN_PTS = { linear: 2, area: 3, count: 1 };
const KMAX = 14;
const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
const r1 = (v) => Math.round(v * 10) / 10;
const dist = (a, b) => Math.hypot(b[0] - a[0], b[1] - a[1]);
const pathLen = (pts) => pts.slice(1).reduce((s, p, i) => s + dist(pts[i], p), 0);
const polyArea = (pts) => Math.abs(pts.reduce((s, p, i) => { const q = pts[(i + 1) % pts.length]; return s + p[0] * q[1] - q[0] * p[1]; }, 0)) / 2;
const svgEl = (tag, attrs = {}, parent) => {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
};
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
};

const vp = $("#viewport"), stage = $("#stage"), img = $("#sheet-img"), svg = $("#overlay");
const gEx = $("#g-existing"), gFocus = $("#g-focus"), gCal = $("#g-cal"), gDraft = $("#g-draft");
const readout = $("#readout"), hintEl = $("#hint");

const S = {
  job: B.job, sheets: B.sheets, conds: B.conditions, sig: JSON.stringify([B.sheets, B.conditions]),
  sheetId: B.sheet, condId: B.cond || null, tool: "select", focus: B.focus || null,
  k: 1, tx: 0, ty: 0, fitK: 1, fitted: true, iw: 0, ih: 0, ips: 1.5, loaded: false,
  meas: [], qty: new Map(), qtyOk: true, measOk: true,
  draft: null, sel: null, cal: null, cur: null, cursor: null, shift: false, space: false, down: null, qtySig: "",
};

const sheet = () => S.sheets.find((s) => s.id === S.sheetId) || null;
const cond = (id = S.condId) => S.conds.find((c) => c.id === id) || null;
const sheetById = (id) => S.sheets.find((s) => s.id === id) || null;
const ptPerFt = (sid = S.sheetId) => sheetById(sid)?.scale?.pt_per_ft || null;
const colorOf = (cid) => cond(cid)?.color || "#8a919d";
// "A1 - 3-5/8\" 20ga ..." -> "3-5/8\" 20ga ...": the tag is shown separately
const shortName = (c) => String(c.name || "").replace(new RegExp("^" + String(c.type_tag).replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\s*[-–:]\\s*"), "");
const sizePt = () => sheet()?.size_pt || [S.iw / S.ips, S.ih / S.ips];

// ---------------------------------------------------------------- coordinates and view
const vpRect = () => vp.getBoundingClientRect();
function toPt(cx, cy) {
  const r = vpRect();
  return [(cx - r.left - S.tx) / S.k / S.ips, (cy - r.top - S.ty) / S.k / S.ips];
}
const toView = (pt) => [pt[0] * S.ips * S.k + S.tx, pt[1] * S.ips * S.k + S.ty];
const toImg = (pt) => [pt[0] * S.ips, pt[1] * S.ips];
function clampPt(p) { const [w, h] = sizePt(); return [clamp(p[0], 0, w), clamp(p[1], 0, h)]; }

function applyView() {
  stage.style.transform = `translate(${S.tx}px, ${S.ty}px) scale(${S.k})`;
  stage.style.setProperty("--inv", String(1 / S.k));
  $("#zoom-label").textContent = Math.round((S.k / S.fitK) * 100) + "%";
  positionCalPop();
}
function fit() {
  if (!S.iw) return;
  const r = vpRect(), pad = 28;
  S.fitK = Math.min((r.width - 2 * pad) / S.iw, (r.height - 2 * pad) / S.ih);
  S.k = S.fitK; S.tx = (r.width - S.iw * S.k) / 2; S.ty = (r.height - S.ih * S.k) / 2;
  S.fitted = true; applyView(); renderDraft();
}
function zoomAt(mx, my, k2) {
  k2 = clamp(k2, S.fitK * 0.4, KMAX);
  const f = k2 / S.k;
  S.tx = mx - (mx - S.tx) * f; S.ty = my - (my - S.ty) * f; S.k = k2; S.fitted = false;
  applyView(); renderDraft();
}
function zoomToBox(box) {  // box in pt: x0, y0, x1, y1
  const [x0, y0, x1, y1] = box.map((v) => v * S.ips), r = vpRect();
  const k = clamp(Math.min((r.width * 0.5) / (x1 - x0), (r.height * 0.5) / (y1 - y0)), S.fitK, 4);
  S.k = k; S.tx = r.width / 2 - ((x0 + x1) / 2) * k; S.ty = r.height / 2 - ((y0 + y1) / 2) * k;
  S.fitted = false; applyView(); renderDraft();
}

// ---------------------------------------------------------------- sheets
function showEmpty(title, sub = "") {
  stage.style.display = "none";
  const e = $("#vp-empty");
  e.innerHTML = `<b>${esc(title)}</b><span>${esc(sub)}</span>`; e.hidden = false;
  S.loaded = false; renderMeas(); renderDraft(); renderFocus(); renderCal(); updateHint(); renderToolbar();
}
function loadImage() {
  const sh = sheet();
  S.loaded = false;
  if (!sh) return showEmpty("No sheets yet", "Upload a drawing set on the Sheets tab to start a takeoff.");
  if (!sh.image_path) return showEmpty("This sheet is still rendering…", "It appears here as soon as its image is ready.");
  $("#vp-empty").hidden = true; stage.style.display = "";
  const done = () => {
    if (sheet()?.id !== sh.id) return;
    S.iw = img.naturalWidth; S.ih = img.naturalHeight;
    S.ips = sh.size_pt ? S.iw / sh.size_pt[0] : sh.image_scale || 1.5;
    stage.style.width = S.iw + "px"; stage.style.height = S.ih + "px";
    svg.setAttribute("width", S.iw); svg.setAttribute("height", S.ih); svg.setAttribute("viewBox", `0 0 ${S.iw} ${S.ih}`);
    S.loaded = true; fit();
    if (S.focus && S.focus.sheet === sh.id) zoomToBox(S.focus.bbox);
    renderAll();
  };
  img.onload = done;
  img.onerror = () => showEmpty("The sheet image didn't load.", sh.image_path);
  const want = new URL(sh.image_path, location.href).href;
  if (img.src === want && img.complete && img.naturalWidth) done(); else img.src = sh.image_path;
}
async function openSheet(id) {
  if (S.draft) finishDraft();
  S.sheetId = id; S.sel = null; S.cal = null; S.meas = []; S.cur = null;
  hideCalPop(); hideMenus(); syncUrl();
  renderRail(); renderToolbar(); loadImage();
  const m = await Takeoff.measurements(id);
  if (id !== S.sheetId) return;
  S.meas = m.ok ? m.items : []; S.measOk = m.ok;
  renderMeas(); renderToolbar();
}
function syncUrl() {
  const q = new URLSearchParams();
  if (S.sheetId) q.set("sheet", S.sheetId);
  if (S.condId) q.set("cond", S.condId);
  history.replaceState(null, "", `/jobs/${S.job.id}/takeoff?${q}`);
}

// ---------------------------------------------------------------- data
async function refresh() {
  const sid = S.sheetId;
  const [m, q] = await Promise.all([sid ? Takeoff.measurements(sid) : { ok: true, items: [] }, Takeoff.quantities(S.job.id)]);
  if (sid === S.sheetId) { S.meas = m.ok ? m.items : []; S.measOk = m.ok; }
  S.qty = new Map(q.rows.map((x) => [x.cid, x])); S.qtyOk = q.ok;
  renderAll();
}
async function reloadData(force = false) {
  const r = await Takeoff.jobData(S.job.id);
  if (!r.ok) return false;
  const sig = JSON.stringify([r.data.sheets, r.data.conditions]);
  if (sig === S.sig && !force) return false;
  S.sig = sig;
  const before = sheet();
  S.sheets = r.data.sheets; S.conds = r.data.conditions; S.job = { ...S.job, ...r.data.job };
  if (!S.sheetId && S.sheets.length) S.sheetId = (S.sheets.find((s) => s.image_path) || S.sheets[0]).id;
  renderRail(); renderConds(); renderToolbar(); renderQty();
  const now = sheet();
  if (now && (!before || before.id !== now.id || before.image_path !== now.image_path)) { loadImage(); refresh(); }
  return true;
}

// ---------------------------------------------------------------- rendering: rail, toolbar, panel, drawer
function renderRail() {
  const rail = $("#rail");
  if (!S.sheets.length) { rail.innerHTML = `<div class="pempty" data-testid="rail-empty">No sheets yet. Upload a drawing set first.</div>`; return; }
  rail.innerHTML = groupSheets(S.sheets).map((g) =>
    `<div class="rgroup" data-testid="rail-group"><i>${esc(g.code)}</i>${esc(g.name)}<span>${g.sheets.length}</span></div>` +
    g.sheets.map((s) => {
      const sc = s.scale, pt = s.kind === "partition_types";
      const chip = sc && sc.pt_per_ft
        ? `<span class="chip ${sc.source === "manual" ? "cal" : ""}" data-testid="sheet-scale-chip" title="${esc(sc.label)} · ${sc.pt_per_ft} pt per ft">${esc(sc.label)}</span>`
        : `<span class="chip warn" data-testid="sheet-scale-chip" title="No usable scale: set or calibrate it before measuring">${sc && sc.label === "NTS" ? "NTS" : "no scale"}</span>`;
      return `<button type="button" class="rrow ${s.id === S.sheetId ? "active" : ""} ${pt ? "pt" : ""}" data-sheet="${s.id}" data-testid="rail-sheet">
        <span class="rthumb">${s.thumb_path ? `<img src="${esc(s.thumb_path)}" alt="" loading="lazy">` : `<i>rendering…</i>`}</span>
        <span class="rtext"><span class="rno"><span data-testid="rail-sheet-no">${esc(s.sheet_no || "p." + s.page)}</span>${pt ? `<em>partitions</em>` : ""}</span><span class="rtitle">${esc(s.title || "Untitled sheet")}</span>
        <span class="rmeta">${chip}</span></span></button>`;
    }).join("")).join("");
  rail.querySelector(".rrow.active")?.scrollIntoView({ block: "nearest" });
}

function renderToolbar() {
  const sh = sheet(), sc = sh?.scale, chip = $("#scale-chip");
  chip.className = "chip " + (sc?.pt_per_ft ? (sc.source === "manual" ? "cal" : "") : "warn");
  chip.textContent = sc?.pt_per_ft ? sc.label : sh ? (sc?.label === "NTS" ? "NTS · set scale" : "No scale") : "—";
  chip.disabled = !sh;
  chip.title = sc?.pt_per_ft ? `${sc.pt_per_ft} pt per real foot · ${scaleNote(sc)}. Click to change.` : "No usable scale on this sheet. Click to set one or calibrate.";
  $$(".tool[data-tool]").forEach((b) => b.classList.toggle("on", b.dataset.tool === S.tool));
  vp.dataset.tool = S.tool;
  $("#noscale").hidden = !(sh && S.loaded && !ptPerFt() && S.tool !== "calibrate" && S.tool !== "select");
  const standin = Takeoff.standins();
  const mc = $("#mode-chip");
  mc.hidden = !standin.length;
  if (standin.length) { mc.textContent = "stand-in API"; mc.title = `These routes are answered by ui/standins.py until their lane merges: ${standin.join(", ")}`; mc.className = "chip warn"; }
}

function condRow(c, i) {
  const tag = esc(c.type_tag), nm = esc(shortName(c));
  const src = c.source && c.source.sheet ? sheetById(c.source.sheet) : null;
  return `<div class="crow ${c.id === S.condId ? "active" : ""}" data-cid="${c.id}" data-testid="cond-row" style="--c:${esc(c.color)}" role="button" tabindex="0" aria-pressed="${c.id === S.condId}">
    <span class="cswatch" data-testid="cond-swatch"></span>
    <div><div class="ctop"><b class="ctag" data-testid="cond-tag">${tag}</b><span class="cname" data-testid="cond-name" title="${esc(c.name)}">${nm}</span></div>
      <div class="cnums"><span data-testid="cond-lf" data-u="lf"><b></b> LF</span><span data-testid="cond-sf" data-u="sf"><b></b> SF</span><span data-testid="cond-ea" data-u="ea"><b></b> EA</span></div></div>
    <div class="cside"><span class="cactive" data-testid="cond-active">ACTIVE</span>
      ${src ? `<button type="button" class="csrc" data-src="${c.id}" data-testid="cond-source" title="Show where ${tag} is defined on ${esc(src.sheet_no || "its sheet")}">${esc(src.sheet_no || "source")} ↗</button>` : `<span class="ckey">${i < 9 ? i + 1 : ""}</span>`}</div>
  </div>`;
}
function renderConds() {
  const list = $("#clist");
  list.innerHTML = S.conds.length ? S.conds.map(condRow).join("")
    : `<div class="pempty" data-testid="cond-empty">No conditions yet. They appear as the partition sheet is read, or add one with + New condition.</div>`;
  updateNumbers();
}

// Quantity for one condition: what the server has, plus unsaved (pending) measurements and the shape being drawn.
function running(cid) {
  const q = S.qty.get(cid) || {};
  const out = { lf: q.lf || 0, sf: q.sf || 0, ea: q.ea || 0, n: q.n || 0, live: false };
  for (const m of S.meas) if (m.pending && m.condition_id === cid) { out[m.unit] += m.quantity; out.n += 1; }
  const d = S.draft && S.draft.cond === cid ? draftMetrics() : null;
  if (d) { out[d.unit] += d.qty; out.live = true; }
  return out;
}
function updateNumbers() {
  for (const row of $$(".crow")) {
    const q = running(row.dataset.cid);
    for (const u of ["lf", "sf", "ea"]) {
      const cell = row.querySelector(`[data-u=${u}]`), v = q[u];
      cell.querySelector("b").textContent = u === "ea" ? String(Math.round(v)) : fmtNum(v, 1);
      cell.classList.toggle("zero", !v);
      cell.classList.toggle("ticking", q.live && v > 0 && S.draft && UNIT[S.draft.kind] === u);
    }
  }
}

function qtyRows() {
  const rows = [];
  for (const c of S.conds) {
    const q = running(c.id);
    if (q.lf || q.sf || q.ea) rows.push({ c, q });
  }
  for (const r of S.qty.values()) if (!S.conds.some((c) => c.id === r.cid) && (r.lf || r.sf || r.ea)) {
    rows.push({ c: { id: r.cid, type_tag: r.type_tag || "?", name: r.name || "", color: r.color || "#8a919d", height_ft: r.height_ft }, q: running(r.cid) });
  }
  return rows;
}
function renderQty() {
  const rows = qtyRows();
  const sig = JSON.stringify([rows.map((r) => [r.c.id, r.q]), S.qtyOk, S.measOk]);
  if (sig === S.qtySig) return;
  S.qtySig = sig;
  const tot = rows.reduce((t, r) => ({ lf: t.lf + r.q.lf, sf: t.sf + r.q.sf, ea: t.ea + r.q.ea }), { lf: 0, sf: 0, ea: 0 });
  $("#qty-sum").textContent = rows.length ? `${rows.length} condition${rows.length === 1 ? "" : "s"} · ${fmtNum(tot.lf, 1)} LF · ${fmtNum(tot.sf, 0)} SF · ${Math.round(tot.ea)} EA` : "nothing measured yet";
  const empty = $("#qty-empty");
  $("#qty-table").hidden = !rows.length;
  empty.hidden = !!rows.length;
  empty.textContent = !S.qtyOk
    ? "The quantities endpoint isn't answering yet, so nothing can be totalled. It lands with the pipeline lane."
    : "No measurements yet. Pick a condition on the right, press L, and click along a wall.";
  const cell = (v, d, cls = "") => `<td class="num ${v ? "" : "zero"} ${cls}">${v ? fmtNum(v, d) : "—"}</td>`;
  $("#qty-body").innerHTML = rows.map(({ c, q }) => `<tr data-testid="qty-row" data-cid="${c.id}">
    <td style="white-space:nowrap"><span class="swatch" style="background:${esc(c.color)}"></span><b>${esc(c.type_tag)}</b> <span class="muted">${esc(shortName(c))}</span></td>
    ${cell(q.lf, 1).replace("<td", '<td data-testid="qty-lf"')}${cell(q.sf, 1).replace("<td", '<td data-testid="qty-sf"')}${cell(q.ea, 0).replace("<td", '<td data-testid="qty-ea"')}
    <td class="num muted">${q.n || ""}</td>
    <td class="num"><button type="button" class="sm" data-order="${c.id}" data-testid="qty-create-order" ${q.lf > 0 && !q.live ? "" : "disabled"} title="${q.lf > 0 ? "Build a priced material order from these linear feet" : "An order is built from linear feet"}">Create order</button></td>
  </tr>`).join("");
}

function renderAll() { renderRail(); renderToolbar(); renderConds(); renderQty(); renderMeas(); renderDraft(); renderFocus(); renderCal(); updateHint(); }

// ---------------------------------------------------------------- rendering: canvas overlay
function labelFor(m) {
  if (m.kind === "linear") return fmtFtIn(m.quantity);
  if (m.kind === "area") return fmtNum(m.quantity, 0) + " SF";
  return "";
}
function measNode(m) {
  const c = colorOf(m.condition_id), P = m.points.map(toImg), pts = P.map((p) => p.join(",")).join(" ");
  const g = svgEl("g", { class: `m ${m.kind}${m.id === S.sel ? " sel" : ""}${m.pending ? " pending" : ""}`, "data-mid": m.id, "data-testid": "measurement" });
  if (m.kind === "count") {
    P.forEach((p, i) => {
      svgEl("circle", { cx: p[0], cy: p[1], class: "pin", fill: c }, g);
      svgEl("text", { x: p[0], y: p[1], class: "pinn" }, g).textContent = String(i + 1);
    });
    return g;
  }
  const tag = m.kind === "area" ? "polygon" : "polyline";
  svgEl(tag, { points: pts, class: "hit" }, g);
  svgEl(tag, { points: pts, class: "vis", stroke: c, fill: m.kind === "area" ? c : "none" }, g);
  if (m.id === S.sel) P.forEach((p) => svgEl("circle", { cx: p[0], cy: p[1], class: "dot", stroke: c }, g));
  // label: middle of the longest run for lines, centroid for areas
  let at;
  if (m.kind === "linear") {
    let best = 0;
    for (let i = 1; i < P.length; i++) if (dist(P[i - 1], P[i]) >= dist(P[best], P[best + 1] || P[best])) best = i - 1;
    at = [(P[best][0] + P[best + 1][0]) / 2, (P[best][1] + P[best + 1][1]) / 2];
  } else at = [P.reduce((s, p) => s + p[0], 0) / P.length, P.reduce((s, p) => s + p[1], 0) / P.length];
  const t = svgEl("text", { x: at[0], y: at[1], dy: m.kind === "linear" ? "-0.7em" : "0.35em", class: "lbl" }, g);
  t.textContent = labelFor(m);
  return g;
}
function renderMeas() {
  gEx.replaceChildren();
  if (!S.loaded) return;
  for (const m of S.meas) gEx.appendChild(measNode(m));
}

function draftMetrics() {
  const d = S.draft;
  if (!d) return null;
  const ppf = ptPerFt(d.sheet);
  const pts = S.cur && d.kind !== "count" ? [...d.points, S.cur] : d.points;
  if (d.kind === "count") return { unit: "ea", qty: d.points.length, text: `×${d.points.length}` };
  if (!ppf) return { unit: UNIT[d.kind], qty: 0, text: "no scale" };
  if (d.kind === "linear") {
    const qty = pathLen(pts) / ppf, seg = pts.length > 1 ? dist(pts[pts.length - 2], pts[pts.length - 1]) / ppf : 0;
    return { unit: "lf", qty, seg, text: fmtFtIn(qty), segText: fmtFtIn(seg) };
  }
  const qty = pts.length >= 3 ? polyArea(pts) / (ppf * ppf) : 0;
  return { unit: "sf", qty, text: fmtNum(qty, 0) + " SF" };
}
function renderDraft() {
  gDraft.replaceChildren();
  const d = S.draft;
  if (!d || d.sheet !== S.sheetId) { readout.hidden = true; updateNumbers(); renderQty(); return updateHint(); }
  const c = colorOf(d.cond), P = d.points.map(toImg), cur = S.cur && d.kind !== "count" ? toImg(S.cur) : null;
  const all = cur ? [...P, cur] : P;
  if (d.kind === "area" && all.length >= 3) svgEl("polygon", { points: all.map((p) => p.join(",")).join(" "), class: "vis area", fill: c, stroke: "none" }, gDraft);
  if (d.kind === "count") P.forEach((p) => { svgEl("circle", { cx: p[0], cy: p[1], class: "pin", fill: c, style: "r:calc(8px*var(--inv));stroke:#fff;stroke-width:calc(2px*var(--inv))" }, gDraft); });
  else {
    if (P.length > 1) svgEl("polyline", { points: P.map((p) => p.join(",")).join(" "), class: "vis", stroke: c }, gDraft);
    if (cur && P.length) svgEl("line", { x1: P[P.length - 1][0], y1: P[P.length - 1][1], x2: cur[0], y2: cur[1], class: "vis rubber", stroke: c }, gDraft);
    if (cur && d.kind === "area" && P.length >= 2) svgEl("line", { x1: cur[0], y1: cur[1], x2: P[0][0], y2: P[0][1], class: "vis rubber", stroke: c, opacity: ".55" }, gDraft);
    if (d.kind === "area" && P.length >= 3) svgEl("circle", { cx: P[0][0], cy: P[0][1], class: "first", stroke: c }, gDraft);
    P.forEach((p) => svgEl("circle", { cx: p[0], cy: p[1], class: "dot", stroke: c }, gDraft));
  }
  const m = draftMetrics();
  if (S.cursor && m) {
    readout.hidden = false;
    readout.innerHTML = d.kind === "linear" && m.segText ? `${esc(m.text)}<small>+${esc(m.segText)}</small>` : esc(m.text);
    readout.style.left = S.cursor[0] + "px"; readout.style.top = S.cursor[1] + "px";
  } else readout.hidden = true;
  updateNumbers(); renderQty(); updateHint();
}

function renderFocus() {
  gFocus.replaceChildren();
  const f = S.focus;
  if (!f || f.sheet !== S.sheetId || !S.loaded) return;
  const [x0, y0, x1, y1] = f.bbox.map((v) => v * S.ips);
  svgEl("rect", { x: x0, y: y0, width: x1 - x0, height: y1 - y0, rx: 5, class: "focusbox", "data-testid": "source-box" }, gFocus);
  svgEl("text", { x: x0, y: y0, dy: "-0.5em", class: "focuslbl" }, gFocus).textContent = `${f.tag} source`;
}
function renderCal() {
  gCal.replaceChildren();
  const c = S.cal;
  if (!c || !S.loaded) return;
  const a = toImg(c.p1), b = c.p2 ? toImg(c.p2) : S.cur ? toImg(S.cur) : null;
  if (b) {
    svgEl("line", { x1: a[0], y1: a[1], x2: b[0], y2: b[1] }, gCal);
    svgEl("text", { x: (a[0] + b[0]) / 2, y: (a[1] + b[1]) / 2, dy: "-0.8em" }, gCal).textContent = `${Math.round(dist(c.p1, c.p2 || S.cur))} pt`;
  }
  svgEl("circle", { cx: a[0], cy: a[1] }, gCal);
  if (c.p2) svgEl("circle", { cx: b[0], cy: b[1] }, gCal);
}

function updateHint() {
  const sh = sheet(), c = cond();
  let html = "";
  if (!sh || !S.loaded) html = `<span class="dim">${sh ? "Loading the sheet…" : "No sheet open"}</span>`;
  else if (S.focus && S.focus.sheet === sh.id && S.tool === "select") html = `Source of <b>${esc(S.focus.tag)}</b> on ${esc(sh.sheet_no || "this sheet")} <button type="button" id="clear-focus">Clear</button>`;
  else if (S.tool === "select") html = `<b>Select</b> <span class="dim">· click a measurement, Delete removes it · pick a condition to measure</span>`;
  else if (S.tool === "calibrate") html = `<b>Calibrate</b> <span class="dim">· ${S.cal && !S.cal.p2 ? "click the second point" : "click two points on a dimension you know"}</span>`;
  else if (!c) html = `<span class="dim">Pick a condition on the right to measure</span>`;
  else {
    const m = S.draft ? draftMetrics() : null;
    html = `<span class="dot" style="background:${esc(c.color)}"></span><b>${esc(c.type_tag)}</b> <span class="dim">· ${KIND_NAME[S.tool]}</span>` +
      (m ? ` · <b>${esc(m.text)}</b> <span class="dim">${m.unit === "lf" ? fmtNum(m.qty, 2) + " LF · " : ""}Enter to finish · Esc ${S.draft.points.length >= MIN_PTS[S.draft.kind] ? "saves" : "cancels"}</span>`
        : ` <span class="dim">· ${S.tool === "count" ? "click each item, Enter to finish" : S.tool === "area" ? "click the corners, double-click to close" : "click along the wall, double-click to finish"}</span>`);
  }
  if (!S.measOk && sh) html += ` <span class="dim">· measurements for this sheet couldn't load</span>`;
  hintEl.innerHTML = html;
}

// ---------------------------------------------------------------- tools
function setTool(t) {
  if (S.draft) finishDraft();
  S.tool = t; S.cal = null; S.cur = null; hideCalPop(); hideMenus();
  if (t !== "select") S.sel = null;
  renderToolbar(); renderMeas(); renderCal(); renderDraft();
}
function setCond(id, arm = true) {
  if (S.draft && S.draft.cond !== id) finishDraft();
  S.condId = id; syncUrl(); renderConds();
  if (arm && (S.tool === "select" || S.tool === "calibrate")) setTool("linear"); else { renderToolbar(); updateHint(); }
}
function flash(el) { el.classList.remove("flash"); void el.offsetWidth; el.classList.add("flash"); }
function needCondition() { toast("Pick a condition on the right first.", true); flash($("#panel")); }
function needScale() { toast("Set or calibrate this sheet's scale first.", true); flash($("#noscale")); }

function snap(p) {  // shift: constrain the next point to 45 degree steps from the last
  const d = S.draft;
  if (!S.shift || !d || !d.points.length || d.kind === "count") return p;
  const a = d.points[d.points.length - 1], dx = p[0] - a[0], dy = p[1] - a[1], len = Math.hypot(dx, dy);
  const ang = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4);
  return [a[0] + len * Math.cos(ang), a[1] + len * Math.sin(ang)];
}
function pointAt(e) { return clampPt(snap(toPt(e.clientX, e.clientY))); }

function addPoint(p) {
  const c = cond();
  if (!c) return needCondition();
  if ((S.tool === "linear" || S.tool === "area") && !ptPerFt()) return needScale();
  if (!S.draft || S.draft.kind !== S.tool) S.draft = { kind: S.tool, points: [], cond: c.id, sheet: S.sheetId };
  const d = S.draft;
  if (d.kind === "area" && d.points.length >= 3) {  // clicking the first corner closes the area
    const a = toView(p), b = toView(d.points[0]);
    if (Math.hypot(a[0] - b[0], a[1] - b[1]) < 10) return finishDraft();
  }
  d.points.push(p); S.cur = p;
  renderDraft();
}
function cancelDraft() { S.draft = null; S.cur = null; renderDraft(); }
function localQty(kind, pts, sid) {
  const ppf = ptPerFt(sid);
  if (kind === "count") return pts.length;
  if (!ppf) return 0;
  return kind === "linear" ? pathLen(pts) / ppf : polyArea(pts) / (ppf * ppf);
}
async function finishDraft() {
  const d = S.draft;
  if (!d) return;
  if (d.points.length < MIN_PTS[d.kind]) return cancelDraft();
  S.draft = null; S.cur = null;
  const c = cond(d.cond), pts = d.points.map((p) => [r1(p[0]), r1(p[1])]);
  const body = { job_id: S.job.id, sheet: d.sheet, condition_id: d.cond, kind: d.kind, points: pts, height_ft: c?.height_ft ?? null };
  const tmp = { id: "tmp-" + Date.now(), ...body, quantity: localQty(d.kind, pts, d.sheet), unit: UNIT[d.kind], pending: true };
  if (d.sheet === S.sheetId) S.meas.push(tmp);
  renderMeas(); renderDraft(); renderQty();
  const r = await Takeoff.addMeasurement(body);
  if (!r.ok) {
    toast(errText(r, "Couldn't save that measurement."), true);
    S.meas = S.meas.filter((m) => m !== tmp);
    if (!S.draft && d.sheet === S.sheetId) { S.draft = d; S.cur = null; }  // keep the work so it can be retried
  }
  await refresh();
  renderToolbar();
}
async function undo() {
  if (S.draft && S.draft.points.length) {
    S.draft.points.pop();
    if (!S.draft.points.length) S.draft = null;
    return renderDraft();
  }
  const r = await Takeoff.undo(S.sheetId);
  if (!r.ok) return toast(errText(r, "Nothing to undo."), true);
  S.sel = null; await refresh();
}
async function deleteSelected() {
  const id = S.sel;
  if (!id || String(id).startsWith("tmp-")) return;
  S.sel = null; S.meas = S.meas.filter((m) => m.id !== id);
  renderMeas(); renderQty();
  const r = await Takeoff.deleteMeasurement(id);
  if (!r.ok) toast(errText(r, "Couldn't delete that measurement."), true);
  await refresh();
}
function select(id) { S.sel = id; renderMeas(); }

function onEscape() {
  if (!$("#help").hidden || !$("#scale-menu").hidden) return hideMenus();
  if (S.draft) return S.draft.points.length >= MIN_PTS[S.draft.kind] ? finishDraft() : cancelDraft();
  if (S.cal) { S.cal = null; hideCalPop(); renderCal(); return updateHint(); }
  if (S.sel) return select(null);
  if (S.focus) return clearFocus();
  if (S.tool !== "select") setTool("select");
}
function clearFocus() { S.focus = null; renderFocus(); updateHint(); }

// ---------------------------------------------------------------- calibrate
function calPoint(p) {
  if (!S.cal || S.cal.p2) { S.cal = { p1: p, p2: null }; hideCalPop(); }
  else {
    if (dist(S.cal.p1, p) < 2) return;
    S.cal.p2 = p; showCalPop();
  }
  renderCal(); updateHint();
}
function showCalPop() {
  const c = S.cal, pop = $("#cal-pop");
  $("#cal-dist").textContent = `${Math.round(dist(c.p1, c.p2))} pt`;
  $("#cal-err").textContent = ""; $("#cal-len").value = "";
  pop.hidden = false; positionCalPop(); $("#cal-len").focus();
}
function positionCalPop() {
  const pop = $("#cal-pop");
  if (pop.hidden || !S.cal || !S.cal.p2) return;
  const [x, y] = toView(S.cal.p2), r = vpRect();
  pop.style.left = clamp(x + 16, 8, r.width - pop.offsetWidth - 8) + "px";
  pop.style.top = clamp(y + 12, 56, r.height - pop.offsetHeight - 8) + "px";
}
function hideCalPop() { $("#cal-pop").hidden = true; }
async function applyCal() {
  const c = S.cal, ft = parseLength($("#cal-len").value);
  if (!c || !c.p2) return;
  if (!ft || ft <= 0) { $("#cal-err").textContent = `Type a length like 24'-6" or 24.5`; return; }
  const btn = $("#cal-apply"); btn.disabled = true;
  const r = await Takeoff.calibrate(S.sheetId, c.p1.map(r1), c.p2.map(r1), ft);
  btn.disabled = false;
  if (!r.ok) { $("#cal-err").textContent = errText(r, "Couldn't calibrate."); return; }
  S.cal = null; hideCalPop(); renderCal();
  await reloadData(true);
  const ppf = ptPerFt();
  toast(`Calibrated: ${fmtNum(ppf, 2)} pt per foot${ppf ? ` (1:${fmtNum(864 / ppf, 0)})` : ""}`);
  setTool(cond() ? "linear" : "select");
}

// ---------------------------------------------------------------- menus
function hideMenus() { $("#scale-menu").hidden = true; $("#help").hidden = true; }
function showScaleMenu() {
  const m = $("#scale-menu"), cur = sheet()?.scale?.label;
  if (!m.hidden) return (m.hidden = true);
  $("#help").hidden = true;
  m.innerHTML = B.scaleLabels.filter((l) => l !== "NTS").map((l) => `<button type="button" data-label="${esc(l)}" class="${l === cur ? "cur" : ""}">${esc(l)}</button>`).join("") +
    `<hr><button type="button" data-cal style="font-family:system-ui;color:var(--accent-text)">Calibrate on a known dimension… (K)</button>`;
  m.style.top = "8px"; m.style.right = "10px"; m.hidden = false;
}
async function chooseScale(label) {
  hideMenus();
  const r = await Takeoff.setScale(S.sheetId, label);
  if (!r.ok) return toast(errText(r, "Couldn't set that scale."), true);
  await reloadData(true); toast(`Scale set to ${label}`);
}

// ---------------------------------------------------------------- events: canvas
vp.addEventListener("pointerdown", (e) => {
  if (e.target.closest(".hint, .banner, .pop, .menu")) return;
  if (e.button === 2) return;
  vp.setPointerCapture(e.pointerId);
  const mid = e.target.closest("[data-mid]");
  S.down = { x: e.clientX, y: e.clientY, btn: e.button, tx: S.tx, ty: S.ty, moved: false, mid: mid ? mid.dataset.mid : null,
    pan: e.button === 1 || S.space || (S.tool === "select" && e.button === 0 && !mid) };
  if (e.button === 1) e.preventDefault();
});
vp.addEventListener("pointermove", (e) => {
  const r = vpRect();
  S.cursor = [e.clientX - r.left, e.clientY - r.top]; S.shift = e.shiftKey;
  const d = S.down;
  if (d) {
    const dx = e.clientX - d.x, dy = e.clientY - d.y;
    if (!d.moved && Math.hypot(dx, dy) > 4) d.moved = true;
    if (d.pan && d.moved) { S.tx = d.tx + dx; S.ty = d.ty + dy; S.fitted = false; vp.classList.add("panning"); applyView(); renderDraft(); return; }
  }
  if (S.loaded && (S.draft || (S.cal && !S.cal.p2))) { S.cur = pointAt(e); renderDraft(); if (S.cal) renderCal(); }
});
vp.addEventListener("pointerup", (e) => {
  const d = S.down; S.down = null;
  vp.classList.remove("panning");
  if (!d || d.btn !== 0) return;
  if (d.pan && d.moved) return;
  if (Math.hypot(e.clientX - d.x, e.clientY - d.y) > 6) return;
  if (!S.loaded) return;
  const p = pointAt(e);
  if (S.tool === "select") select(d.mid);
  else if (S.tool === "calibrate") calPoint(p);
  else addPoint(p);
});
vp.addEventListener("pointercancel", () => { S.down = null; vp.classList.remove("panning"); });
vp.addEventListener("pointerleave", () => { if (!S.down) { readout.hidden = true; } });
vp.addEventListener("dblclick", (e) => {
  if (e.target.closest(".hint, .banner, .pop, .menu") || !S.draft) return;
  const d = S.draft, n = d.points.length;
  if (n >= 2) { const a = toView(d.points[n - 1]), b = toView(d.points[n - 2]); if (Math.hypot(a[0] - b[0], a[1] - b[1]) < 8) d.points.pop(); }  // the 2nd click of the double-click
  finishDraft();
});
vp.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  if (S.draft) cancelDraft(); else if (S.cal) { S.cal = null; hideCalPop(); renderCal(); updateHint(); }
});
vp.addEventListener("wheel", (e) => {
  e.preventDefault();
  if (!S.loaded) return;
  const r = vpRect(), unit = e.deltaMode === 1 ? 16 : 1;
  zoomAt(e.clientX - r.left, e.clientY - r.top, S.k * Math.exp(-e.deltaY * unit * (e.ctrlKey ? 0.01 : 0.0017)));
}, { passive: false });
new ResizeObserver(() => { if (S.fitted && S.loaded) fit(); else positionCalPop(); }).observe(vp);
hintEl.addEventListener("click", (e) => { if (e.target.id === "clear-focus") clearFocus(); });

// ---------------------------------------------------------------- events: keyboard
document.addEventListener("keydown", (e) => {
  const t = e.target, typing = /^(INPUT|SELECT|TEXTAREA)$/.test(t.tagName) || t.isContentEditable;
  if (e.key === "Shift") { S.shift = true; if (S.draft) { S.cur = S.cur && snap(S.cur); renderDraft(); } return; }
  if (typing) return;
  if (e.key === " ") { if (t.tagName === "BUTTON") t.blur(); S.space = true; vp.classList.add("space"); e.preventDefault(); return; }
  const k = e.key.toLowerCase();
  if ((e.ctrlKey || e.metaKey) && k === "z") { e.preventDefault(); return undo(); }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  if (k === "enter") { if (t.tagName === "BUTTON" || t.classList.contains("crow")) return; return void finishDraft(); }
  if (k === "escape") return onEscape();
  if (k === "delete" || k === "backspace") { e.preventDefault(); return S.draft ? undo() : void deleteSelected(); }
  const tools = { v: "select", l: "linear", a: "area", c: "count", k: "calibrate" };
  if (tools[k]) return setTool(tools[k]);
  if (k === "f") return fit();
  if (k === "q") return toggleDrawer();
  if (k === "?") return void ($("#scale-menu").hidden = true, $("#help").hidden = !$("#help").hidden);
  if (k === "+" || k === "=") return zoomAt(vpRect().width / 2, vpRect().height / 2, S.k * 1.25);
  if (k === "-") return zoomAt(vpRect().width / 2, vpRect().height / 2, S.k / 1.25);
  if (/^[1-9]$/.test(k) && S.conds[+k - 1]) setCond(S.conds[+k - 1].id);
});
document.addEventListener("keyup", (e) => {
  if (e.key === "Shift") { S.shift = false; if (S.draft) renderDraft(); }
  if (e.key === " ") { S.space = false; vp.classList.remove("space"); if (document.activeElement?.tagName === "BUTTON") e.preventDefault(); }
});
window.addEventListener("blur", () => { S.space = false; S.shift = false; vp.classList.remove("space"); });

// ---------------------------------------------------------------- events: toolbar, rail, panel, drawer
$$(".tool[data-tool]").forEach((b) => b.addEventListener("click", () => { setTool(b.dataset.tool); b.blur(); }));
$("#btn-fit").addEventListener("click", (e) => { fit(); e.currentTarget.blur(); });
$("#btn-undo").addEventListener("click", (e) => { undo(); e.currentTarget.blur(); });
$("#btn-help").addEventListener("click", (e) => { $("#scale-menu").hidden = true; $("#help").hidden = !$("#help").hidden; e.stopPropagation(); });
$("#scale-chip").addEventListener("click", (e) => { showScaleMenu(); e.stopPropagation(); });
$("#scale-menu").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.cal !== undefined) { hideMenus(); setTool("calibrate"); } else chooseScale(b.dataset.label);
});
document.addEventListener("click", (e) => { if (!e.target.closest("#scale-menu, #help, #scale-chip, #btn-help")) hideMenus(); });
$("#rail").addEventListener("click", (e) => { const row = e.target.closest("[data-sheet]"); if (row && row.dataset.sheet !== S.sheetId) openSheet(row.dataset.sheet); });

$("#clist").addEventListener("click", (e) => {
  const src = e.target.closest("[data-src]");
  if (src) return void goSource(src.dataset.src);
  const row = e.target.closest("[data-cid]");
  if (row) setCond(row.dataset.cid);
});
$("#clist").addEventListener("keydown", (e) => {
  if (e.key !== "Enter") return;
  const row = e.target.closest("[data-cid]");
  if (row) { e.preventDefault(); setCond(row.dataset.cid); }
});
async function goSource(cid) {  // jump to the condition's partition sheet with its box highlighted
  const c = cond(cid), s = c?.source;
  if (!s || !s.sheet) return;
  S.focus = s.bbox ? { condition: c.id, tag: c.type_tag, sheet: s.sheet, bbox: s.bbox } : null;
  if (s.sheet !== S.sheetId) { setTool("select"); await openSheet(s.sheet); }
  else { setTool("select"); renderFocus(); if (S.focus) zoomToBox(S.focus.bbox); updateHint(); }
}

// new condition form
const form = $("#cond-form");
const nextColor = () => B.palette.find((p) => !S.conds.some((c) => c.color === p)) || B.palette[S.conds.length % B.palette.length];
$("#cond-new").addEventListener("click", () => {
  form.hidden = !form.hidden;
  if (!form.hidden) { form.elements.color.value = nextColor(); $("#cond-err").textContent = ""; form.elements.type_tag.focus(); }
});
$("#cond-cancel").addEventListener("click", () => { form.hidden = true; });
$("#cond-swatches").addEventListener("click", (e) => { const b = e.target.closest("[data-color]"); if (b) form.elements.color.value = b.dataset.color; });
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(form), tag = String(f.get("type_tag") || "").trim();
  if (!tag) return;
  const body = {
    type_tag: tag, color: f.get("color"),
    studs: { size_in: +f.get("size_in"), gauge: f.get("gauge") ? +f.get("gauge") : null, spacing_in: f.get("spacing_in") ? +f.get("spacing_in") : null },
    layers_each_side: Array.from({ length: +f.get("layers") }, () => ({ thickness_in: +f.get("thickness_in"), board_type: f.get("board_type") })),
    sides: +f.get("sides"),
  };
  if (String(f.get("name")).trim()) body.name = String(f.get("name")).trim();
  if (f.get("height_ft")) body.height_ft = +f.get("height_ft");
  if (f.get("fire_rating")) body.fire_rating = +f.get("fire_rating");
  const btn = form.querySelector("[type=submit]"); btn.disabled = true;
  const r = await Takeoff.createCondition(S.job.id, body);
  btn.disabled = false;
  if (!r.ok) { $("#cond-err").textContent = errText(r, "Couldn't add that condition."); return; }
  await reloadData(true);
  const id = (r.data && (r.data.condition_id || r.data.id || Takeoff.idOf(r.data))) || S.conds.find((c) => c.type_tag === tag)?.id;
  form.reset(); form.hidden = true;
  if (id && cond(id)) { setCond(id); toast(`Added ${tag}`); }
});

// quantities drawer
function toggleDrawer(open) {
  const d = $("#drawer"), want = open === undefined ? d.classList.contains("closed") : open;
  d.classList.toggle("closed", !want);
  $("#qty-toggle").setAttribute("aria-expanded", String(want));
  store.set("mat.qtyOpen", want ? "1" : "0");
}
$("#qty-toggle").addEventListener("click", (e) => { toggleDrawer(); e.currentTarget.blur(); });
$("#qty-body").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-order]");
  if (!btn || btn.disabled) return;
  const cid = btn.dataset.order, c = cond(cid), q = S.qty.get(cid) || running(cid);
  btn.disabled = true; btn.textContent = "Creating…";
  const r = await Takeoff.createOrder(S.job.id, cid, Math.round((q.lf || 0) * 10) / 10, c?.height_ft || 10);
  if (r.ok && r.orderId) { location.href = "/orders/" + r.orderId; return; }
  toast(errText(r, "Couldn't create the order."), true);
  btn.disabled = false; btn.textContent = "Create order";
});

// calibrate popover and the no-scale banner
$("#cal-apply").addEventListener("click", applyCal);
$("#cal-cancel").addEventListener("click", () => { S.cal = null; hideCalPop(); renderCal(); updateHint(); });
$("#cal-len").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { e.preventDefault(); applyCal(); }
  if (e.key === "Escape") { e.preventDefault(); $("#cal-cancel").click(); }
});
$("#noscale-cal").addEventListener("click", () => setTool("calibrate"));
$("#quick-scale").innerHTML += B.scaleLabels.filter((l) => l !== "NTS").map((l) => `<option>${esc(l)}</option>`).join("");
$("#quick-scale").addEventListener("change", (e) => { if (e.target.value) chooseScale(e.target.value); e.target.value = ""; });

// ---------------------------------------------------------------- start
toggleDrawer(store.get("mat.qtyOpen", "1") !== "0");
renderRail(); renderConds(); renderToolbar(); updateHint();
(async () => {
  if (S.condId) setCond(S.condId);
  loadImage();
  await refresh();
  renderToolbar();
})();
setInterval(() => { if (document.visibilityState === "visible" && !S.draft) reloadData(); }, 6000);
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") reloadData(); });
window.TakeoffDebug = { S, toPt, toView, fit };
})();
