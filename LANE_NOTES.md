# Lane notes

Lanes don't edit `app/`, `contracts/` or another lane's folder. When you need a change there, append an entry
here on your own branch, work around it for now, and keep going. The integrator reads this file at every merge.

## Template
```
### <lane> · <HH:MM> · <one-line title>
Need: what has to change, in which file (a new field, index, helper, prompt, route...).
Why: what it unblocks.
Workaround in place: what you did meanwhile, so the integrator can remove it.
Status: open
```

## Entries

### pipeline · 14:40 · FYI for the merge: what lane 2 now owns and the behaviours worth knowing
Need: nothing from you; this is what changed and where, so the merge and the UI wiring don't surprise anyone.
- Files: `pipeline/` (ewo, orders, takeoff, events, api), `orders/math.py` (lines_for; every constant is in `CONSTANTS`),
  `po/render.py`, `po/send.py`, `po/api.py`, `po/__init__.py` (exposes `po.render` and `po.send` per the contracts README; that
  makes `po.send` the function, so import the module as `from po.send import send`), `tests/pipeline`, `tests/orders`.
  `pipeline/_lines_stub.py` is gone: `pipeline.orders` calls `orders.math.lines_for`.
- `GET /events/pm` has no `/api` prefix (as the UI expects); everything else is under `/api`. Each SSE message is the full
  `ewo_drafts` doc as JSON with `id` beside `_id`. On mongomock (no change streams) it polls instead.
- Guard: a line is over when `qty > ceil(computed_qty x (1 + waste))`, i.e. the allowance is rounded up to a whole purchase unit,
  so math that orders in whole boxes or sheets never trips its own guard. `guard.items_over` holds item keys.
  `PATCH /api/orders/{id}/lines/{i}` also refreshes `total`, and returns 409 once the order has `sent_at`.
- `POST /api/ewos` takes an optional `room_id` (the foreman's dropdown choice) and uses it when it is a room on that sheet and job;
  otherwise `$near` decides. Routing runs as a background task; a crash there parks the EWO in review_queue instead of leaving it on "new".
- Routing: a task needs `enabled: true` in `model_policy` to be offered to Jev. With exactly one enabled task Jev is skipped
  (routing.model is `rules:single-option`); with none, or when all System One models fail, the EWO goes to review_queue.
  Jev usage rows are logged under task `ewo_route`.
- Drafts: `total` is recomputed as sum(qty x unit_cost) in code; if the model's figure differed it is kept as `total_model`.
- `POST /api/orders` and `POST /api/jobs/{job}/conditions/{cid}/order` both answer `{order_id, id, total, unpriced, guard, order}`.
- Email is off: `po.send` writes `outbox {order_id, job_id, to, subject, html, at}` and sets `orders.sent_at` and `supplier_email`.
Workaround in place: none needed.
Status: info

### pipeline · 14:40 · price_book only covers part of what a parsed condition can ask for
Need: in `contracts/seed.py`, price_book rows for stud/track gauges 22, 14, 12 and 30, and boards at 0.75 / 0.25 in,
or a decision that those items stay unpriced. Today `orders.math` emits `stud_3.625_22ga_10ft` for a 22 ga condition and the
`$lookup` finds nothing.
Why: a partition parsed with a gauge the price book lacks produces unpriced lines (total counts only priced ones).
Workaround in place: unknown items keep `unit_cost: null`, are listed in the `unpriced` field of the order responses, and the PO shows "unpriced".
Status: open
