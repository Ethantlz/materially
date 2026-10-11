You own extract/ and tests/extract/ in the worktree C:\Users\ethan\materially-extract (branch lane/extract). Don't edit app/, contracts/, ui/ or other lanes' folders; if you need a change there, write it in LANE_NOTES.md and work around it. Read docs/SPEC.md sections 4, 6 and 8 and contracts/README.md first.

Run everything against your own database with the shared keys:
    $env:DB_NAME = "lane1"; infisical run --env=dev -- uv run pytest tests/extract -q
    $env:DB_NAME = "lane1"; infisical run --env=dev -- uv run uvicorn app.main:app --port 8001
Use the shared fixtures in tests/conftest.py: `live_db` (Atlas lane1, real validators; skipped outside `infisical run`) and `mock_db` (mongomock with seeds). Every model call goes through app.jev or app.router.run_task; never call a model directly. LiteLLM caps DeepSeek at 30 requests/min, so run at most 4 run_task calls at once.

Already built and working; extend it, don't rewrite it:
- extract/pdftext.py: pdfium text segments (milliseconds per page, unlike pdfplumber on dense CAD sheets)
- extract/sheetinfo.py: sheet number, title, scale, and kind by title keywords
- extract/scale.py: scale parsing to PDF points per foot
- extract/ingest.py: pass 1 writes named + scaled sheets within seconds, pass 2 renders page PNGs
- extract/api.py: POST /api/jobs (upload), GET /api/jobs/{id}, PATCH /api/sheets/{id}/scale
tests/test_upload_flow.py and scripts/make_drawing_set.py must keep passing. The synthetic 7-sheet ARCH D set includes A-501 PARTITION TYPES with tags A1, A2, B1 and rooms 101/102/103 on the plan sheets.

Build:
1. Title-block locator. sheetinfo.PageText already has a tb_corner field. Add locate_title_blocks(pages, common): from title-block text that repeats on at least half the sheets, decide per page whether the title block is a right strip or a bottom band and set tb_corner; call it in ingest.sheet_docs right after common_title_block_text. Today view titles near the bottom edge of a right-strip sheet are read as title block (synthetic A-102 reports scale source title_block; EXPECTED says view_title). Assert scale_source in the test.
2. Sheet kind by Jev, after pass 1 so names still appear instantly: app.jev.choice(title-block text + sheet title, SHEET_KINDS, "Which kind of drawing sheet is this?", job_id=job_id). Agreed: set kind and routing. Not agreed, or Jev errors: keep the keyword kind, store routing, and to_review(...). At most 8 Jev calls at once.
3. Partition sheets -> conditions. Find type tags (short tokens like A1, P-3, 1A, 3B), group the segments near each tag into one block (assign each segment to its nearest tag within that tag's column or cell), run_task("parse_partition", block, "conditions", job_id=job_id), then insert_or_review(db, "conditions", {**result, "job_id": ..., "source": {"sheet": <sheet _id hex>, "page": ..., "bbox": [...]}, "routing": {"model": ..., "escalated": ...}}, "extract.partition"). On TaskFailed, to_review with the reason. A MongoDB rejection must land in review_queue; that's a demo beat.
4. Floor plans -> rooms. Room-number tokens (101, 204A) stacked with an uppercase name become rooms {job_id, sheet: <sheet _id hex>, name, number, label_pt: [x, y]}, where label_pt is the label centre in PDF points. Skip duplicates per sheet.
5. Routes in extract/api.py: GET /api/jobs/{job_id}/sheets, GET /api/jobs/{job_id}/conditions, GET /api/jobs/{job_id}/rooms, and POST /api/sheets/{sheet_id}/parse, which re-runs partition parsing on a sheet picked by hand (the Checkpoint A fallback).
6. Tests in tests/extract/:
   - tag finding and block grouping on synthetic A-501: exactly A1, A2, B1, and each block contains its stud line
   - rooms on A-101: 101/102/103, each label_pt inside its room rectangle
   - the title-block locator
   - a live test (skip unless LITELLM_API_KEY is set): ingest the synthetic set into lane1 and find 3 conditions in MongoDB with studs.size_in 3.625 and gauge 20
   - if fixtures/demo.pdf and fixtures/expected/demo.json exist, print field accuracy per type tag (stud size, gauge, spacing, layers, board type, fire rating)
7. Budget: a 50-sheet set shows every sheet name within 5 s. Jev and partition parsing run afterwards and update the docs in place.

Done when `$env:DB_NAME="lane1"; infisical run --env=dev -- uv run pytest tests/extract tests/test_upload_flow.py -q` passes, and an upload of the synthetic set on port 8001 shows A-501's three conditions at /api/jobs/{id}/conditions. Commit and push lane/extract every hour. No PDFs in git, no secrets in code.
