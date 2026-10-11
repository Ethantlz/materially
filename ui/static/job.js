// Sheet grid: renders from /api/jobs/{id} and polls until the job is ready.
const grid = document.getElementById("grid");
const bar = document.getElementById("bar");
const statusEl = document.getElementById("status");
const KIND = {
  partition_types: "Partition types", floor_plan: "Floor plan", reflected_ceiling: "RCP",
  elevations: "Elevations", details: "Details", schedules: "Schedules", other: "Other",
};
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cards = new Map();

function scaleSelect(sheet) {
  const cur = sheet.scale?.label;
  const labels = [...new Set([...(cur ? [cur] : []), ...SCALE_LABELS])];
  const opts = (cur ? "" : `<option value="" selected>Not found — set scale</option>`) +
    labels.map(l => `<option ${l === cur ? "selected" : ""}>${esc(l)}</option>`).join("");
  return `<select data-testid="sheet-scale" data-id="${sheet.id}">${opts}</select>`;
}

function scaleNote(s) {
  if (!s) return "no scale found";
  if (s.source === "manual") return "set by you";
  const src = { title_block: "title block", view_title: "view titles", bookmark: "bookmark" }[s.source] || s.source;
  return `auto · ${src}` + (s.candidates?.length > 1 ? ` · also ${s.candidates.filter(c => c !== s.label).join(", ")}` : "");
}

function render(sheet) {
  let el = cards.get(sheet.id);
  if (!el) {
    el = document.createElement("article");
    el.className = "sheet";
    el.dataset.testid = "sheet-card";
    cards.set(sheet.id, el);
    grid.appendChild(el);
  }
  const key = JSON.stringify([sheet.thumb_path, sheet.sheet_no, sheet.title, sheet.scale?.label, sheet.scale?.source]);
  if (el.dataset.key === key) return;
  el.dataset.key = key;
  const thumb = sheet.thumb_path
    ? `<img src="${sheet.thumb_path}" alt="" loading="lazy" data-full="${sheet.image_path}">`
    : `<div class="ph">rendering…</div>`;
  el.className = "sheet" + (sheet.kind === "partition_types" ? " hot" : "");
  el.innerHTML = `
    <div class="thumb">${thumb}</div>
    <div class="meta">
      <div class="row"><span class="no" data-testid="sheet-no">${esc(sheet.sheet_no || "—")}</span>
        <span class="kind" data-testid="sheet-kind">${esc(KIND[sheet.kind] || sheet.kind)}</span></div>
      <div class="title" data-testid="sheet-title">${esc(sheet.title || "Untitled sheet")}</div>
      <div class="scale">${scaleSelect(sheet)}</div>
      <div class="muted small" data-testid="sheet-scale-source">${esc(scaleNote(sheet.scale))}</div>
    </div>`;
}

grid.addEventListener("change", async (e) => {
  const sel = e.target.closest("select[data-id]");
  if (!sel || !sel.value) return;
  const res = await fetch(`/api/sheets/${sel.dataset.id}/scale`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label: sel.value }),
  });
  if (res.ok) poll(true);
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
  const res = await fetch(`/api/jobs/${JOB_ID}`);
  if (!res.ok) return;
  const { job, sheets } = await res.json();
  sheets.forEach(render);
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
