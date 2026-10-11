You own pipeline/ and tests/pipeline/ in the worktree C:\Users\ethan\materially-pipeline (branch lane/pipeline). Don't edit app/, contracts/, orders/ or other lanes' folders; requests go in LANE_NOTES.md. Read docs/SPEC.md sections 2, 4, 5 and 6 and contracts/README.md first.

Run against your own database with the shared keys:
    $env:DB_NAME = "lane2"; infisical run --env=dev -- uv run pytest tests/pipeline -q
    $env:DB_NAME = "lane2"; infisical run --env=dev -- uv run uvicorn app.main:app --port 8002
Use the shared fixtures in tests/conftest.py: `live_db` (Atlas lane2, needed for validators, $near and change streams; skipped outside `infisical run`) and `mock_db` (mongomock with seeds, for pure logic). Use a fresh job_id per test and delete what you insert. pipeline/ewo.py and pipeline/orders.py already hold the signature stubs other lanes call: keep those signatures exactly and fill in the bodies. Every model call goes through app.jev or app.router.run_task. DeepSeek is capped at 30 requests/min.

Contracts you build on:
- References are _id hex strings; rooms.sheet and ewos.sheet are the sheets._id hex.
- Coordinates are PDF points; rooms has the index {label_pt: "2d", sheet: 1} with min 0, max 5000.
- Write AI output with app.db.insert_or_review; a validator rejection lands in review_queue with the reason.
- model_policy rows exist for price_extra, base_scope_check and backcharge (tier quick, DeepSeek, retry Sonnet); their prompts are in app/prompts.py.
- price_book item keys are documented in contracts/seed.py (supplier "Sample Supply Co").

Build:
1. pipeline/ewo.py create(job_id, sheet, x, y, description) -> ewo _id hex: room = rooms.find_one({label_pt: {$near: [x, y], $maxDistance: 400}, sheet, job_id}); insert into ewos {job_id, sheet, pin: [x, y], room_id (or None), description, submitted_by: "foreman, synthetic", status: "new", created_at}.
2. route(ewo_id) -> draft _id hex, or None when it went to review_queue:
   - Options are the model_policy tasks in (price_extra, base_scope_check, backcharge) with enabled true, read live on every call, so disabling one in Compass stops Jev picking it.
   - Ask app.jev.choice(state = EWO description + room name/number, options with one-line descriptions, "Which task handles this extra work order?", job_id=job_id).
   - Not agreed: to_review and status "review".
   - Agreed: run_task(task, {ewo, room, the job's conditions (type_tag, studs, layers_each_side, fire_rating), relevant price_book rows}, "ewo_drafts", job_id=job_id), then insert_or_review ewo_drafts {**result, ewo_id, job_id, task, model, escalated, routing: <jev result>} and set status "drafted".
   - TaskFailed: to_review and status "review".
   - Run route as a background task right after create.
3. GET /events/pm: SSE fed by a change stream on ewo_drafts inserts (full document), with an optional ?job_id filter, a heartbeat comment every 15 s, and a clean close when the client disconnects. Run the blocking watch() in a thread and pass events to the async generator through a queue.
4. pipeline/orders.py build(job_id, condition_id, lf, height_ft) -> order _id hex:
   - lines = orders.math.lines_for(condition, lf, height_ft) if importable; otherwise use your stub in pipeline/_lines_stub.py with exactly that signature and line shape {item, qty, unit, computed_qty, waste}. H2 owns orders/.
   - Insert the unpriced order, then price it inside MongoDB with orders.aggregate([$match _id, $unwind lines, $lookup price_book on lines.item = item, set lines.unit_cost, $group back with total = sum(qty x unit_cost), $merge into orders on _id whenMatched replace]).
   - Unknown items keep unit_cost null and are listed in the response.
5. guard(order_id) -> guard dict, as an update pipeline: items_over = lines where qty > computed_qty x (1 + waste); set guard {over, items_over}. PATCHing a line's qty re-runs the guard (the UI's "padded line trips the guard").
6. GET /api/jobs/{job_id}/cost: $group on usage -> {calls, cost_usd, by_task: [{task, model, calls, cost_usd}]}.
7. Routes in pipeline/api.py, prefix /api: POST /ewos, GET /ewos?job_id, POST /ewos/{id}/route, GET /drafts?job_id, POST /orders, GET /orders/{id}, PATCH /orders/{id}/lines/{i}, POST /orders/{id}/guard, GET /jobs/{job_id}/cost. Plus GET /events/pm.

Tests in tests/pipeline/:
- a pin near a seeded room label returns that room; a far pin returns None
- a routed EWO inserts a draft (live: skip unless LITELLM_API_KEY is set)
- an invalid draft (monkeypatch run_task to return a negative qty) lands in review_queue with MongoDB's reason
- build gives a priced order with total > 0 from seeded price_book keys
- a padded line trips the guard and an unpadded one doesn't
- the SSE stream emits within 10 s of an insert
- the cost endpoint sums usage rows

Done when `$env:DB_NAME="lane2"; infisical run --env=dev -- uv run pytest tests/pipeline -q` passes. Commit and push lane/pipeline every hour. No secrets in code.
