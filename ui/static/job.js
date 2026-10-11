// Sheets tab: renders sheets from /api/jobs/{id}, grouped by discipline, and polls until the job is ready.
const grid = document.getElementById("grid");
const bar = document.getElementById("bar");
const statusEl = document.getElementById("status");
const cards = new Map();
const groupEls = new Map();

function scaleSelect(sheet) {
  const cur = sheet.scale?.label;
  const labels = [...new Set([...(cur ? [cur] : []), ...SCALE_LABELS])];
  const opts = (cur ? "" : `<option value="" selected>Not found — set scale</option>`) +
    labels.map(l => `<option ${l === cur ? "selected" : ""}>${esc(l)}</option>`).join("");
  return `<select class="${cur ? "" : "none"}" data-testid="sheet-scale" data-id="${sheet.id}" title="Sheet scale">${opts}</select>`;
}

function render(sheet) {
  let el = cards.get(sheet.id);
  if (!el) {
    el = document.createElement("article");
    el.className = "sheet";
    el.dataset.testid = "sheet-card";
    cards.set(sheet.id, el);
  }
  const key = JSON.stringify([sheet.thumb_path, sheet.sheet_no, sheet.title, sheet.scale?.label, sheet.scale?.source, sheet.kind, sheet.routing]);
  if (el.dataset.key === key) return el;
  el.dataset.key = key;
  const thumb = sheet.thumb_path
    ? `<img src="${sheet.thumb_path}" alt="" loading="lazy" data-full="${sheet.image_path}">`
    : `<div class="ph">rendering…</div>`;
  el.className = "sheet" + (sheet.kind === "partition_types" ? " hot" : "");
  const r = sheet.routing;
  const jev = !r || !("probabilities" in r || "choice" in r) ? "" : r.choice
    ? `<span class="jev" data-testid="sheet-jev" title="Jev agreed twice with shuffled options">Jev ${Math.round(100 * (r.probabilities?.[r.choice] ?? 0))}%</span>`
    : `<span class="jev warn" data-testid="sheet-jev" title="Jev's two answers disagreed; sent to review">Jev: review</span>`;
  el.innerHTML = `
    <div class="thumb">${thumb}</div>
    <div class="meta">
      <div class="row"><span class="no" data-testid="sheet-no">${esc(sheet.sheet_no || "—")}</span>
        <span>${jev} <span class="kind" data-testid="sheet-kind">${esc(KIND_LABEL[sheet.kind] || sheet.kind)}</span></span></div>
      <div class="title" data-testid="sheet-title">${esc(sheet.title || "Untitled sheet")}</div>
      <div class="scale">${scaleSelect(sheet)}</div>
      <div class="muted small" data-testid="sheet-scale-source">${esc(scaleNote(sheet.scale))}</div>
      <a class="openlink" href="/jobs/${JOB_ID}/takeoff?sheet=${sheet.id}" data-testid="sheet-open">Open in takeoff →</a>
    </div>`;
  return el;
}

// One section per discipline, in plan-set order; existing cards are moved, not rebuilt.
function layout(sheets) {
  for (const g of groupSheets(sheets)) {
    let sec = groupEls.get(g.code);
    if (!sec) {
      sec = document.createElement("section");
      sec.className = "group";
      sec.dataset.testid = "sheet-group";
      sec.innerHTML = `<h3></h3><div class="grid"></div>`;
      groupEls.set(g.code, sec);
    }
    sec.querySelector("h3").innerHTML = `<i>${esc(g.code)}</i>${esc(g.name)} · ${g.sheets.length} sheet${g.sheets.length === 1 ? "" : "s"}`;
    const inner = sec.querySelector(".grid");
    g.sheets.forEach(s => inner.appendChild(render(s)));
    grid.appendChild(sec);
  }
}

grid.addEventListener("change", async (e) => {
  const sel = e.target.closest("select[data-id]");
  if (!sel || !sel.value) return;
  const r = await Takeoff.setScale(sel.dataset.id, sel.value);
  if (r.ok) poll(true); else toast(errText(r, "Couldn't set the scale"), true);
});

const viewer = document.getElementById("viewer");
grid.addEventListener("click", (e) => {
  const img = e.target.closest(".thumb img");
  if (!img) return;
  const card = img.closest(".sheet");
  document.getElementById("vimg").src = img.dataset.full;
  document.getElementById("vtitle").textContent =
    card.querySelector(".no").textContent + "  " + card.querySelector(".title").textContent;
  viewer.showModal();
});
document.getElementById("vclose").onclick = () => viewer.close();

async function poll(once = false) {
  const res = await Jobs.get(JOB_ID);
  if (!res.ok) return;
  const { job, sheets } = res.data;
  layout(sheets);
  const total = job.pages_total || 0;
  const done = (job.pages_named || 0) + (job.pages_rendered || 0);
  bar.style.width = total ? `${Math.round((100 * done) / (2 * total))}%` : "4%";
  const scaled = sheets.filter(s => s.scale?.pt_per_ft).length;
  statusEl.textContent = job.status === "error" ? `Error: ${job.error}`
    : job.status === "ready" ? `${sheets.length} sheets · ${scaled} scaled automatically`
    : `Reading sheets… ${job.pages_named || 0}/${total || "?"} named, ${job.pages_rendered || 0} rendered`;
  bar.parentElement.classList.toggle("done", job.status === "ready");
  if (!once && job.status !== "ready" && job.status !== "error") setTimeout(poll, 700);
}
poll();
