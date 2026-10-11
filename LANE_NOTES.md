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
