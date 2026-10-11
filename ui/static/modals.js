// One dialog for every popup on the takeoff screen. A partial's own <script data-init> runs after it loads.
(() => {
  const dlg = document.getElementById("modal");
  if (!dlg) return;
  const body = () => dlg.querySelector(".modal-body");

  // Each open() takes a number; a response only lands if no newer open() (or a close) has happened since it started.
  let latest = 0;
  async function open(title, url) {
    const mine = ++latest;
    dlg.querySelector("h2").textContent = title;
    body().innerHTML = '<div class="empty">Loading…</div>';
    if (!dlg.open) dlg.showModal();
    let html;
    try {
      const r = await fetch(url);
      html = r.ok ? await r.text() : `<div class="empty">Couldn't load this (${r.status}).</div>`;
    } catch (e) {
      html = `<div class="empty">Couldn't load this: the server didn't answer.</div>`;
    }
    if (mine !== latest) return;
    body().innerHTML = html;
    body().querySelectorAll("script[data-init]").forEach((s) => new Function(s.textContent)());
  }
  function close() { dlg.close(); }

  dlg.querySelector("[data-testid=modal-close]").addEventListener("click", () => close());
  dlg.addEventListener("close", () => { latest++; document.dispatchEvent(new CustomEvent("modal:closed")); });
  window.Modal = { open, close };

  // The alternate the takeoff is showing, read from its dropdown (takeoff.js's S isn't visible from this script).
  const alt = () => document.getElementById("alt-select")?.value || "";
  const bind = (id, title, url) => {
    const b = document.getElementById(id);
    if (b) b.onclick = () => Modal.open(title, url());
  };
  bind("open-orders", "Material orders", () => `/ui/jobs/${JOB_ID}/panel/orders?alt=${alt()}`);
  // The drawer's "Orders →" link opens the same popup; its href is the full-page fallback where this script isn't loaded.
  const drawerOrders = document.getElementById("drawer-orders");
  if (drawerOrders) drawerOrders.onclick = (e) => { e.preventDefault(); document.getElementById("open-orders")?.click(); };
  bind("open-ewos", "EWOs", () => `/ui/jobs/${JOB_ID}/panel/ewos`);
  bind("open-tracking", "Tracking", () => `/ui/jobs/${JOB_ID}/panel/tracking?alt=${alt()}`);
  bind("open-review", "Review queue", () => `/ui/jobs/${JOB_ID}/panel/review`);
  // The supplier sites product searches use: the same list on every job, so no job id in the url.
  bind("open-sources", "Suppliers", () => "/ui/panel/sources");

  // A link inside a popup stays in the takeoff: an order link (the orders list, the tracking list) opens that order in the
  // same dialog, and a link with data-modal-open (Order's "← Orders") opens that panel. Both keep a plain href as the
  // full-page fallback, which only runs where this script isn't loaded.
  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-order-open]");
    if (a) { e.preventDefault(); return Modal.open("Order", `/ui/orders/${a.dataset.orderOpen}/panel`); }
    const l = e.target.closest("[data-modal-open]");
    if (l) { e.preventDefault(); Modal.open(l.dataset.modalTitle || "", l.dataset.modalOpen); }
  });
})();
