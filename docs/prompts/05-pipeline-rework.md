REWORK for lane/pipeline (worktree C:\Users\ethan\materially-pipeline, DB lane2). Finish what you're on (EWO create/route, PM SSE, orders build/guard, cost). Then add the following; it turns the pipeline into the back end of a takeoff tool.

Direction change: materially is becoming a full takeoff tool (sheet list, drawing canvas, conditions panel, click-to-measure, quantities -> order). Video order: upload -> conditions library -> measure walls -> quantities -> order (guard trips on a padded line) -> send PO -> EWO pin -> live PM queue -> cost badge. You own the back end from "measure" onwards.

First: `git pull --rebase origin main` for the new `measurements` contract (contracts/schemas.py and samples.py, b923394). Read its comment and the README note.

1. pipeline/takeoff.py, the measurements back end:
   - add_measurement(job_id, sheet_id, condition_id, kind, points, height_ft=None) -> measurement _id hex.
   - Read the sheet's scale.pt_per_ft. If it's missing, raise a clear error ("set or calibrate the sheet scale first").
   - quantity by kind: linear = sum of segment lengths / pt_per_ft (LF); area = shoelace area / pt_per_ft^2 (SF); count = number of points (EA).
   - Store pt_per_ft on the doc. Write it with insert_or_review.
   - Also: list per sheet, delete one, undo the last measurement on a sheet.
   - quantities(job_id): an aggregation that $groups measurements by condition_id and unit, $lookups the condition's name, color and type_tag, and returns rows sorted by type_tag.
2. Order from takeoff: build_from_takeoff(job_id, condition_id). LF is the condition's total linear quantity; height comes from the condition's height_ft, else the measurements' height_ft, else 10. Call your existing build(); guard runs as before.
3. orders/math.py. H2 isn't running, so you own orders/ and tests/orders/ now. Implement lines_for(condition, lf, height_ft) with exactly the contract signature and line shape, using price_book keys from contracts/seed.py. Put every constant in one dict at the top so Ethan can swap in his own numbers. Placeholder defaults:

   | Line | Quantity | Waste |
   |---|---|---|
   | Studs | ceil(LF x 12 / spacing_in) + 1; length = smallest stock >= height (8/9/10/12/14/16) | 10% |
   | Track | ceil(LF x 2 / 10) | 5% |
   | Board | ceil(LF x height x layers_each_side x sides / sheet_area), with a 4x8/4x10/4x12 sheet >= height | 10% |
   | Screws (1-1/4") | 1 per sf of board, in boxes of 1000 | 5% |
   | Mud (all-purpose box) | finished sf / 450 | 10% |
   | Tape (500 ft roll) | finished sf x 0.38 / 500 | 5% |
   | Insulation batts | LF x height sf, only when the condition has insulation | 5% |
   | Corner bead | 0 unless passed in | - |

   computed_qty is before waste; qty is rounded up after waste. Test 100 LF, 10 ft, 3-5/8" 20 ga @ 16", one layer 5/8" Type X each side by hand in tests/orders.
4. po/, since H1 isn't running the PO part. Email is off today.
   - po/render.py render(order_id): HTML with job name, supplier "Sample Supply Co", lines (item, qty, unit, unit cost, extension) and the total.
   - po/send.py send(order_id): insert into an `outbox` collection {order_id, job_id, to: supplier_email or "orders@sample-supply.example", subject, html, at} and set orders.sent_at.
   - Routes: POST /api/orders/{id}/send and GET /api/orders/{id}/po (returns the HTML).
5. New routes (prefix /api): POST /measurements, GET /sheets/{sheet_id}/measurements, DELETE /measurements/{id}, POST /sheets/{sheet_id}/measurements/undo, GET /jobs/{job_id}/quantities, POST /jobs/{job_id}/conditions/{condition_id}/order.
6. Tests:
   - 864 pt at 9 pt/ft = 96 LF; a 2-pt square at 9 pt/ft gives the right SF
   - no scale -> a clear error
   - quantities groups two conditions correctly
   - build_from_takeoff gives a priced order with total > 0
   - the hand-worked lines_for case
   - send() writes to the outbox and sets sent_at

Done when `$env:DB_NAME="lane2"; infisical run --env=dev -- uv run pytest tests/pipeline tests/orders -q` passes. Commit after each step; push lane/pipeline.
