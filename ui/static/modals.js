// One dialog for every popup on the takeoff screen. A partial's own <script data-init> runs after it loads.
(() => {
  const dlg = document.getElementById("modal");
  if (!dlg) return;
  const body = () => dlg.querySelector(".modal-body");

  async function open(title, url) {
    dlg.querySelector("h2").textContent = title;
    body().innerHTML = '<div class="empty">Loading…</div>';
    if (!dlg.open) dlg.showModal();
    const r = await fetch(url);
    body().innerHTML = r.ok ? await r.text() : `<div class="empty">Couldn't load this (${r.status}).</div>`;
    body().querySelectorAll("script[data-init]").forEach((s) => new Function(s.textContent)());
  }
  function close() { dlg.close(); }

  dlg.querySelector("[data-testid=modal-close]").addEventListener("click", () => close());
  dlg.addEventListener("close", () => document.dispatchEvent(new CustomEvent("modal:closed")));
  window.Modal = { open, close };

  // The alternate the takeoff is showing (Task 9 sets S.altId); S is a top-level const in takeoff.js.
  const alt = () => (typeof S !== "undefined" && S.altId) || "";
  const bind = (id, title, url) => {
    const b = document.getElementById(id);
    if (b) b.onclick = () => Modal.open(title, url());
  };
  bind("open-orders", "Material orders", () => `/ui/jobs/${JOB_ID}/panel/orders?alt=${alt()}`);
  bind("open-ewos", "EWOs", () => `/ui/jobs/${JOB_ID}/panel/ewos`);
  bind("open-tracking", "Tracking", () => `/ui/jobs/${JOB_ID}/panel/tracking?alt=${alt()}`);
  bind("open-review", "Review queue", () => `/ui/jobs/${JOB_ID}/panel/review`);

  // An order link inside a popup (the orders list, the tracking list) opens the order in the same dialog.
  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-order-open]");
    if (a) { e.preventDefault(); Modal.open("Order", `/ui/orders/${a.dataset.orderOpen}/panel`); }
  });
})();
