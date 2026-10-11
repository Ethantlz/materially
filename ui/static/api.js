// Every call the UI makes to a server lives in this file, so wiring to a lane's real route is a one-place edit.
//
// A route lists candidate URLs in order: the lane's real endpoint first, then (for the takeoff routes) the
// stand-in in ui/standins.py. A candidate that answers "route missing" (FastAPI's 404 {"detail":"Not Found"}
// or a 405) is skipped and remembered, so the real route takes over by itself the moment its lane merges.
// A 404 with any other detail ("No such sheet.") is a real answer and is returned as is.
window.API = {
  buildOrder: () => ["/api/orders"],                                          // pipeline
  orderLine: (id, i) => [`/api/orders/${id}/lines/${i}`],                     // pipeline (PATCH)
  guard: (id) => [`/api/orders/${id}/guard`],                                 // pipeline
  sendPO: (id) => [`/api/orders/${id}/send`, `/orders/${id}/send`],           // pipeline
  po: (id) => [`/api/orders/${id}/po`],                                       // pipeline (HTML)
  procure: (id) => [`/api/conditions/${id}/procure`, `/conditions/${id}/procure`], // H1
  ewos: () => ["/api/ewos"],                                                  // pipeline
  events: (job) => `/events/pm?job_id=${encodeURIComponent(job)}`,            // pipeline (SSE)
  draftCard: (id) => `/ui/drafts/${id}/card`,                                 // ui
  cost: (job) => `/ui/jobs/${job}/cost`,                                      // ui
};

window.esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const routeMissing = (res, data) => res.status === 405 || (res.status === 404 && (!data || data.detail === undefined || data.detail === "Not Found"));

// Returns {ok, status, data, via, miss}. via = index of the candidate that answered; miss = every candidate was a missing route.
window.call = async (method, urls, body, opts = {}) => {
  let last = { ok: false, status: 404, data: { detail: "Not available yet: that lane hasn't merged." }, via: -1, miss: true };
  for (let i = opts.from || 0; i < urls.length; i++) {
    let res;
    const form = typeof FormData !== "undefined" && body instanceof FormData;
    try {
      res = await fetch(urls[i], {
        method, headers: body && !form ? { "Content-Type": "application/json" } : {}, body: body ? (form ? body : JSON.stringify(body)) : undefined,
      });
    } catch (e) {
      return { ok: false, status: 0, data: { detail: "The server didn't answer." }, via: i, miss: false };
    }
    let data = null, text = null;
    if (opts.text && res.ok) { text = await res.text(); data = { html: text }; }
    else { try { data = await res.json(); } catch { data = {}; } }
    last = { ok: res.ok, status: res.status, data, via: i, miss: false };
    if (!routeMissing(res, data)) return last;
    last.miss = true;
  }
  return last;
};

window.errText = (r, fallback) => {
  const d = r && r.data && r.data.detail;
  return typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg).join("; ") : fallback;
};

window.toast = (msg, bad = false) => {
  let el = document.getElementById("toast");
  if (!el) { el = document.createElement("div"); el.id = "toast"; el.dataset.testid = "toast"; }
  // an open <dialog> sits in the top layer and hides everything under it: the toast goes inside it while one is open
  const host = document.querySelector("dialog[open]") || document.body;
  if (el.parentNode !== host) host.appendChild(el);
  el.textContent = msg; el.className = bad ? "show bad" : "show";
  clearTimeout(el._t); el._t = setTimeout(() => (el.className = ""), 4200);
};

// ---- the job-cost badge in the top bar, with a per-task breakdown on click ----
window.costBadge = (job) => {
  const btn = document.getElementById("cost"), pop = document.getElementById("cost-pop");
  let seen = null;
  const money = (v) => "$" + v.toFixed(v < 0.1 ? 4 : 2);
  const tick = async () => {
    try {
      const r = await fetch(API.cost(job));
      if (!r.ok) return;
      const { calls, cost_usd, by_task = [] } = await r.json();
      document.getElementById("cost-n").textContent = calls;
      document.getElementById("cost-usd").textContent = money(cost_usd);
      if (seen !== null && seen !== calls) { btn.classList.remove("bump"); void btn.offsetWidth; btn.classList.add("bump"); }
      seen = calls;
      pop.innerHTML = `<h3>${money(cost_usd)}</h3>
        <div class="muted small">${calls} AI decision${calls === 1 ? "" : "s"} on this job${calls ? ` · about ${money(cost_usd / calls)} each` : ""}. Every call is a row in the usage collection.</div>
        ${by_task.length ? `<table>${by_task.map((t) => `<tr><td>${esc(t.task)}<div class="model">${esc(String(t.model || "").split("/").pop())}</div></td><td>${t.calls}</td><td>${money(t.cost_usd)}</td></tr>`).join("")}</table>` : ""}`;
    } catch {}
  };
  btn.addEventListener("click", (e) => { e.stopPropagation(); pop.hidden = !pop.hidden; btn.setAttribute("aria-expanded", String(!pop.hidden)); });
  document.addEventListener("click", (e) => { if (!pop.hidden && !pop.contains(e.target)) { pop.hidden = true; btn.setAttribute("aria-expanded", "false"); } });
  tick(); setInterval(tick, 5000);
};

// ---- takeoff data layer (used by static/takeoff.js) ----
(() => {
  const pick = {};  // route name -> index of the candidate that last answered for real
  const route = async (name, method, urls, body, opts = {}) => {
    const r = await call(method, urls, body, { ...opts, from: pick[name] || 0 });
    if (!r.miss && r.via >= 0) pick[name] = r.via;
    return r;
  };
  const list = (data, ...keys) => {
    if (Array.isArray(data)) return data;
    for (const k of keys) if (data && Array.isArray(data[k])) return data[k];
    return [];
  };
  const idOf = (x) => (x && (x.id || (typeof x._id === "string" ? x._id : x._id && x._id.$oid))) || null;

  window.Jobs = {
    create: (formData) => route("jobCreate", "POST", ["/api/jobs"], formData),                            // extract
    get: (job) => route("job", "GET", [`/api/jobs/${job}`]),                                              // extract
  };

  window.Pm = {
    html: (path) => route("html:" + path, "GET", [path], null, { text: true }),                           // ui (re-fetch a server-rendered page)
    draftCard: (id) => route("draftCard", "GET", [API.draftCard(id)], null, { text: true }),               // ui
    draftIds: (job) => route("draftIds", "GET", [`/ui/jobs/${job}/drafts`]),                               // ui
  };

  window.Takeoff = {
    idOf,
    // names of takeoff routes that are answered by ui/standins.py because the lane's route isn't merged yet
    standins: () => Object.keys(pick).filter((k) => pick[k] > 0 && k !== "order"),

    jobData: (job) => route("jobData", "GET", [`/ui/jobs/${job}/data`]),                                  // ui
    sheet: (id) => route("sheet", "GET", [`/api/sheets/${id}`, `/ui/sheets/${id}`]),                       // extract
    setScale: (id, label) => route("setScale", "PATCH", [`/api/sheets/${id}/scale`], { label }),            // extract
    calibrate: (id, p1, p2, realFt) =>                                                                    // extract
      route("calibrate", "POST", [`/api/sheets/${id}/calibrate`, `/ui/standin/sheets/${id}/calibrate`], { p1, p2, real_ft: realFt }),
    createCondition: (job, body) =>                                                                       // extract
      route("createCondition", "POST", [`/api/jobs/${job}/conditions`, `/ui/standin/jobs/${job}/conditions`], body),
    scan: (job) => route("scan", "POST", [`/api/jobs/${job}/conditions/scan`]),                           // extract
    findProducts: (cid, key) => route("findProducts", "POST", [`/api/conditions/${cid}/materials/${encodeURIComponent(key)}/products`]), // procure
    products: (cid) => route("products", "GET", [`/api/conditions/${cid}/products`]),                     // procure
    procure: (cid) => route("procure", "POST", [`/api/conditions/${cid}/procure`]),                       // procure (all materials)

    async measurements(sheet, alt) {                                                                      // pipeline
      const q = alt ? `?alternate_id=${encodeURIComponent(alt)}` : "";
      const r = await route("measurements", "GET", [`/api/sheets/${sheet}/measurements${q}`, `/ui/standin/sheets/${sheet}/measurements`]);
      return { ...r, items: r.ok ? list(r.data, "measurements", "items").map((m) => ({ ...m, id: idOf(m) })) : [] };
    },
    addMeasurement: (m) => route("addMeasurement", "POST", ["/api/measurements", "/ui/standin/measurements"], m),
    deleteMeasurement: (id) => route("deleteMeasurement", "DELETE", [`/api/measurements/${id}`, `/ui/standin/measurements/${id}`]),
    undo: (sheet, alt) =>
      route("undo", "POST", [`/api/sheets/${sheet}/measurements/undo${alt ? `?alternate_id=${encodeURIComponent(alt)}` : ""}`, `/ui/standin/sheets/${sheet}/measurements/undo`]),

    // One entry per condition: {cid, lf, sf, ea, n, ...}. The rows may come one per (condition, unit) or already merged.
    async quantities(job, alt) {                                                                          // pipeline
      const q = alt ? `?alternate_id=${alt}` : "";
      const r = await route("quantities", "GET", [`/api/jobs/${job}/quantities${q}`, `/ui/standin/jobs/${job}/quantities${q}`]);
      const by = new Map();
      if (r.ok) for (const row of list(r.data, "rows", "quantities", "items")) {
        const key = row._id && typeof row._id === "object" ? row._id : {};
        const cid = row.condition_id || key.condition_id || row.cid;
        if (!cid) continue;
        const o = by.get(cid) || { cid, lf: 0, sf: 0, ea: 0, n: 0 };
        const unit = String(row.unit || key.unit || "").toLowerCase();
        const q = Number(row.quantity ?? row.total ?? row.qty ?? row.sum ?? 0) || 0;
        if (["lf", "sf", "ea"].includes(unit)) o[unit] += q;
        for (const u of ["lf", "sf", "ea"]) if (!unit && row[u] != null) o[u] += Number(row[u]) || 0;
        o.n += Number(row.count ?? row.n ?? row.measurements ?? 0) || 0;
        for (const f of ["type_tag", "name", "color", "height_ft"]) if (row[f] != null) o[f] = row[f];
        by.set(cid, o);
      }
      return { ...r, rows: [...by.values()] };
    },

    // Order from takeoff (pipeline); until that route lands, the manual order route with the measured LF.
    async createOrder(job, cid, lf, heightFt, alt) {
      const q = alt ? `?alternate_id=${alt}` : "";
      let r = await route("order", "POST", [`/api/jobs/${job}/conditions/${cid}/order${q}`]);
      if (r.miss) r = await route("orderManual", "POST", API.buildOrder(), { job_id: job, condition_id: cid, lf, height_ft: heightFt });
      return { ...r, orderId: r.ok ? r.data.order_id || r.data.id || idOf(r.data) || idOf(r.data.order) : null };
    },
  };
})();
