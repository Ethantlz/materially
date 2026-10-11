# Contracts

Only the integrator edits `app/` and `contracts/`. If a lane needs a change here, write it in `LANE_NOTES.md` on the lane branch.

| File | What |
|---|---|
| `schemas.py` | `$jsonSchema` validator per collection (SPEC §4), `INDEXES`, and `EXTRA["product_attrs"]` for model output |
| `validate.py` | `validate(name, doc, ai=False)` → list of errors, the same rules MongoDB applies |
| `samples.py` | one valid doc per collection, all on `JOB_ID`; `BAD_CONDITION` must be rejected |
| `seed.py` | `model_policy` rows and the made-up `price_book` (item key format in its docstring) |
| `setup_db.py` | `uv run python -m contracts.setup_db` → lane1 lane2 lane3 h1 h2 demo (or name one) |
| `TESTIDS.md` | data-testids, appended by lane 3 |

## Conventions
- References (`job_id`, `sheet`, `condition_id`, `room_id`, `ewo_id`) are the referenced doc's `_id` as a hex string.
  `sheet` on rooms and ewos is the `sheets._id` hex, not the sheet number.
- Coordinates are PDF points, origin top-left, y down; `label_pt` and `pin` are `[x, y]`.
- Inches are decimals (3-5/8" → 3.625), `fire_rating` is hours, gauge is an integer.
- Every collection rejects invalid writes (`validationAction: "error"`). Write AI output with
  `app.db.insert_or_review(db, collection, doc, source)`: a rejected doc lands in `review_queue` with the reason.
- `MONGODB_URI=mongomock://` gives an in-memory DB for tests (no validation, no change streams).

- `measurements` is one takeoff line on a sheet: `points` in PDF points, `quantity` in `unit` (lf / sf / ea)
  computed with the sheet's `scale.pt_per_ft`, which is stored on the doc. Total LF per condition is a
  `$group` over measurements; that LF feeds `pipeline.orders.build`.
- `alternates` split a project's takeoff: Base bid (kind `base`, made on first use) plus named alternates. Drawings and
  conditions belong to the project; `measurements.alternate_id` and `orders.alternate_id` belong to one alternate.
  A missing `alternate_id` means Base bid.
- `product_sources` is the company-wide list of supplier sites (`domain` is the hostname, lowercased, no `www.`, unique). A product
  search is limited to these domains (Firecrawl `includeDomains`); with none it searches the open web. `product_data.domains` records
  the list a search used and is part of its `query_hash`; `product_data.checked` is false until Jev has spec-checked it.
- `conditions.materials[]` lists what a wall type is built from (`MATERIAL` in schemas.py), built by `extract.materials.derive`.
- `conditions.parent_id` (the parent condition's `_id` hex; missing or null on a top-level condition) marks a sub-condition: the
  same wall type at another `height_ft`, one level deep. It copies its parent's assembly and has its own `_id`, name and colour, so
  measurements, orders and alternates reference it like any condition. A re-scan of the parent updates the children's assembly.

## Shared calls
```python
from app.db import get_db, insert_or_review, to_review, now, oid, jsonable
from app import jev
from app.router import run_task, TaskFailed

r = jev.choice(state, options, "question?", job_id=job_id)
# {"choice": str | None, "agreed": bool, "probabilities": {...}, "model", "ms", "usage", "answers"}
# store r as the doc's routing; choice None (the two order-reversed asks disagree) -> to_review(...)

r = jev.noul(state, "Does this product fail the spec for thickness_in: 0.625?", job_id=job_id)
# {"p_yes": float, "model", "ms", "usage"}; flag when p_yes > 0.7

try:
    out = run_task("parse_partition", text, "conditions", job_id=job_id)
    # {"result": dict, "model": str, "escalated": bool}
except TaskFailed as e:
    to_review(db, "parse_partition", {"text": text}, e.reason)
```
`validator_name` is a collection name or `"product_attrs"`. The model only produces content fields; `job_id`,
`ewo_id`, `task`, `model`, `routing`, `source` and similar are filled in by the caller. Every Jev and run_task
call writes a `usage` row. Default prompts are in `app/prompts.py`; a `prompt` field on a `model_policy` row
overrides them, and `run_task(..., system=...)` overrides both.

## Cross-lane signatures
- `extract.ingest(...)` (lane 1).
- `orders.math.lines_for(condition: dict, lf: float, height_ft: float) -> list[dict]` (H2). Each line is
  `{"item": <price_book key>, "qty": number, "unit": str, "computed_qty": number, "waste": 0-1}`.
  Lane 2 stubs it with this signature until H2 merges.
- `pipeline.ewo.create(job_id, sheet, x, y, description)`, `pipeline.ewo.route(ewo_id)`,
  `pipeline.orders.build(job_id, condition_id, lf, height_ft)`, `pipeline.orders.guard(order_id)` (lane 2).
- `procure.find(condition_id)`, `po.render(order_id)`, `po.send(order_id)` (H1). `po.send` writes to an
  `outbox` collection when SMTP is not set.

## Routes
Each lane adds endpoints to `router` in its own `<lane>/api.py`; `app/main.py` already includes all of them.
`/data/...` serves page images from `DATA_DIR` (default `data/`), and `/static/...` serves `ui/static/`.
