"""Budget: a 50-sheet set shows every sheet name within 5 s (pass 1 only; Jev and parsing come after)."""
import time

import pytest
from reportlab.pdfgen import canvas

from app.db import now, oid
from extract.ingest import name_sheets
from scripts.make_drawing_set import SHEETS, H, W, _drawing, _title_block
from tests.extract.helpers import forget_job

BUDGET_S = 5.0


@pytest.fixture(scope="module")
def fifty_sheets(tmp_path_factory):
    path = tmp_path_factory.mktemp("fifty") / "fifty.pdf"
    c = canvas.Canvas(str(path), pagesize=(W, H))
    for i in range(50):
        _, title, scale, views = SHEETS[i % len(SHEETS)]
        no = f"A-{101 + i}"
        _drawing(c, no, views)
        _title_block(c, no, title, scale)
        c.showPage()
    c.save()
    return path


@pytest.mark.parametrize("db_fixture", ["mock_db", "live_db"])
def test_fifty_sheet_names_within_budget(db_fixture, request, fifty_sheets):
    db = request.getfixturevalue(db_fixture)
    job_id = str(db.jobs.insert_one({"name": "budget", "created_at": now(), "status": "processing"}).inserted_id)
    try:
        t0 = time.perf_counter()
        name_sheets(db, job_id, [fifty_sheets])
        elapsed = time.perf_counter() - t0
        names = [s["sheet_no"] for s in db.sheets.find({"job_id": job_id}).sort("page", 1)]
        assert names == [f"A-{101 + i}" for i in range(50)]
        assert db.jobs.find_one({"_id": oid(job_id)})["pages_named"] == 50
        assert elapsed < BUDGET_S, f"{elapsed:.2f}s for 50 names"
    finally:
        forget_job(db, job_id)
