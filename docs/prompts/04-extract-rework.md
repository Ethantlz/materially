REWORK for lane/extract (worktree C:\Users\ethan\c15-extract, DB lane1). Keep everything from your current prompt; this adds to it and reorders priorities.

Direction change: materially is becoming a full takeoff tool (sheet list, drawing canvas, conditions panel, click-to-measure, quantities -> order). The 90-second video shows, in order: upload a drawing set -> sheet names + scales -> a conditions library built from the partition sheet (one bad parse rejected into review) -> measure walls on a plan -> quantities -> order -> send PO -> EWO. Your lane feeds the first three beats and the scale every measurement depends on.

First: `git pull --rebase origin main`. Main now has a `measurements` contract (contracts/schemas.py, b923394) the takeoff workspace writes to.

1. FIX: scales missed on a real set (highest priority). Test set: C:\Users\ethan\materially\fixtures\ottawa-arch-tender.pdf, a public 10-sheet Canadian architectural tender set; gitignored, never commit it. All 10 sheet numbers and titles already come out right, but no sheet gets a scale. Cause: under each view title, "SCALE" and "1:100" are separate text segments in separate cells (same baseline, about 34 pt apart, size 7.4). METRIC only matches when "SCALE" is in the same segment, and pick_scale checks one segment at a time. Pair a bare ratio or imperial segment with an adjacent "SCALE" label segment (same baseline, within about 6x the text size, label to its left or directly above). Accept a bare "1:N" only when it's paired like that, so times and ratios elsewhere stay out. Expected on that set:
   - A111 and A121: 1:100
   - A201 and A202: 1:50
   - A501: 1:5
   - A001: none or NTS
   Add a regression test that generates a PDF with split SCALE / 1:100 cells. Also add a test that runs only if the Ottawa file exists, asserting all 10 sheet numbers, titles and those scales.
2. Partition parsing on real sets: on the Ottawa set the partitions live in an "ASSEMBLY SCHEDULE" on A001 (classified partition_types, which is correct). Print that sheet's segments, make tag finding handle the codes it actually uses (W1, P1, ID-1 style as well as A1 / 1A), and get those assemblies into conditions. Keep the synthetic A-501 tests passing.
3. Conditions library API (the TakeOps-style library):
   - Every condition gets a `name` (e.g. `A1 - 3-5/8" 20ga @ 16" - 1L 5/8" Type X e.s.`) and a stable `color` (fixed palette, keyed by type_tag) when it's created.
   - Add POST /api/jobs/{job_id}/conditions (manual create, written through insert_or_review so the validator still applies), PATCH /api/conditions/{id}, DELETE /api/conditions/{id}, and POST /api/conditions/{id}/duplicate.
   - GET /api/jobs/{job_id}/conditions returns name, color, source {sheet, page, bbox} and routing.
4. What the canvas needs:
   - GET /api/sheets/{sheet_id} returns image_path, image_scale (px per PDF pt), size_pt and scale.
   - Add POST /api/sheets/{sheet_id}/calibrate {p1: [x, y], p2: [x, y] in PDF points, real_ft}. It sets scale {label: 'calibrated 1:<ratio>', pt_per_ft: distance/real_ft, source: "manual", confidence: 1}.
   - The existing PATCH /api/sheets/{id}/scale stays.
5. Keep: every sheet name visible within 5 s, Jev sort and partition parsing after that, tests/test_upload_flow.py passing.

Done when `$env:DB_NAME="lane1"; infisical run --env=dev -- uv run pytest tests/extract tests/test_upload_flow.py -q` passes, and an Ottawa upload on port 8001 shows correct scales on A111, A121, A201, A202 and A501 plus at least one condition from the assembly schedule. Commit after each step; push lane/extract.
