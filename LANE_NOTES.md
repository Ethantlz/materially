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

### extract · 14:31 · merge notes (no app/ or contracts/ changes needed)
- **`tests/test_upload_flow.py`** (shared file): it now asserts `scale_source`, and sets `MONGODB_URI`, `DB_NAME`,
  `DATA_DIR` and blank model keys per test with `monkeypatch`. It used to set `MONGODB_URI=mongomock://` at import,
  which made every `live_db` test collected in the same run skip.
- **New fields** (allowed by the validators, no schema change):
  - `sheets.title_block_text`: the sheet's own title-block text (boilerplate dropped), which is what Jev reads.
  - `sheets.routing.method`: `jev` (agreed), `jev` + `fallback: "title_keywords"` (the two asks disagreed),
    `title_keywords` + `error` (Jev down), or `manual` + `previous_kind` (POST `/api/sheets/{id}/parse`).
  - `jobs.pages_sorted`: count of sheets Jev has sorted. `status` turns `ready` once rendering, sorting and
    parsing are all done.
  - `conditions.routing` is `{task, model, escalated}`.
- **Jev usage rows** for sheet sorting use `task: "jev.sheet_kind"`. Options are `SHEET_KINDS` with a one-line
  description each (`extract/analyze.py` `KIND_OPTIONS`), so site, roof and cover sheets land in `other` and sections
  in `elevations`, the same as the title-keyword rules.
- **review_queue sources**: `extract.sheet_kind`, `extract.partition` (TaskFailed or a MongoDB rejection),
  `extract.ingest` and `extract.rooms` (rejected writes).
- **Routes**: `GET /api/jobs/{job_id}/sheets|conditions|rooms` (rooms takes `?sheet=`); `POST /api/sheets/{id}/parse`
  replaces that sheet's conditions in the background and returns `{sheet_id, tags}`, or 422 when it finds no tags.
- The prompt names the worktree `C:\Users\ethan\materially-extract`; it is `C:\Users\ethan\c15-extract`.
Status: merged into main (integrator, 14:45)

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

### integrator · 15:00 · gauge and spacing are now nullable (contracts ccd8e78)
Need (lane 2): `orders.math.lines_for` must accept `studs.gauge: null` and `studs.spacing_in: null`. The demo set (WCU A003)
gives stud size, rating and STC but leaves gauge to the specs. Use a default from `CONSTANTS` (20 ga, 16" o.c.; 24" o.c. for
shaft-wall C-H studs) and mark the line as assumed, so the order and PO say "20 ga (assumed)".
Why: without it every WCU condition prices as `stud_3.625_Nonega_10ft` (unpriced).
Status: open

### extract · 15:20 · WCU A003 schedule columns parse end to end (no app/ or contracts/ change needed)
- `extract/partitions.py` has a schedule layout: wall-family labels (SA, SB, ...) that prefix the tags anchor the
  cell grouping, and each tag heading a schedule column is one block: the family callouts, then
  `SCHEDULE COLUMN <tag>:` with one `<row label>: <values>` line per row. Tags outside every family (the WALL TAG
  LEGEND's SA4) are ignored. Sheets without family labels use the tag + nearby text grouping as before.
- Live on lane1: SA4, SC2, SC4, SD4, SH2, SH4, SH6 inserted (DeepSeek, none escalated); SB2 in review_queue with
  `studs.size_in: expected number, got NoneType None; sides: None not one of [1, 2]`. That reason comes from
  run_task's contract check (the same `$jsonSchema` rules, after the Sonnet retry), so SB2 never reaches a MongoDB
  insert. For the beat to show MongoDB itself rejecting, the router would have to hand the failed result to the
  caller instead of raising TaskFailed.
- `tests/extract/test_demo.py` runs when `fixtures/demo.pdf` exists (live part needs LITELLM_API_KEY).
- Removed a stray `<<<<<<< HEAD` line left in this file by the 14:45 merge.
Status: info

