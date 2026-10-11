// Every call the UI makes to another lane's API lives here, so a path change at merge time is one edit.
// Each entry lists candidate URLs; post() tries them in order and skips any that answer 404.
window.API = {
  buildOrder: () => ["/api/orders"],                                          // lane 2
  orderLine: (id, i) => [`/api/orders/${id}/lines/${i}`],                     // lane 2 (PATCH)
  guard: (id) => [`/api/orders/${id}/guard`],                                 // lane 2
  sendPO: (id) => [`/api/orders/${id}/send`, `/orders/${id}/send`],           // H1
  procure: (id) => [`/api/conditions/${id}/procure`, `/conditions/${id}/procure`], // H1
  ewos: () => ["/api/ewos"],                                                  // lane 2
  events: (job) => `/events/pm?job_id=${encodeURIComponent(job)}`,            // lane 2 (SSE)
  draftCard: (id) => `/ui/drafts/${id}/card`,                                 // ui
  cost: (job) => `/ui/jobs/${job}/cost`,                                      // ui
};

window.esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Returns {ok, status, data}. A lane that isn't merged yet answers 404 on every candidate.
window.call = async (method, urls, body) => {
  let last = { ok: false, status: 404, data: { detail: "Not available yet: that lane hasn't merged." } };
  for (const url of urls) {
    const res = await fetch(url, {
      method, headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined,
    });
    let data = null;
    try { data = await res.json(); } catch { data = {}; }
    last = { ok: res.ok, status: res.status, data };
    if (res.status !== 404 && res.status !== 405) return last;
  }
  return last;
};

window.toast = (msg, bad = false) => {
  let el = document.getElementById("toast");
  if (!el) { el = document.createElement("div"); el.id = "toast"; el.dataset.testid = "toast"; document.body.appendChild(el); }
  el.textContent = msg; el.className = bad ? "show bad" : "show";
  clearTimeout(el._t); el._t = setTimeout(() => (el.className = ""), 4000);
};

window.costBadge = (job) => {
  const tick = async () => {
    try {
      const r = await fetch(API.cost(job));
      if (r.ok) {
        const { calls, cost_usd } = await r.json();
        document.getElementById("cost-n").textContent = calls;
        document.getElementById("cost-usd").textContent = "$" + cost_usd.toFixed(cost_usd < 0.1 ? 4 : 2);
      }
    } catch {}
  };
  tick(); setInterval(tick, 8000);
};
