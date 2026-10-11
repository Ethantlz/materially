"""The Scan partitions button reads the same sheets the upload-time parse reads: kind partition_types OR a title that
names partition types / an assembly schedule (a cover page carrying the ASSEMBLY SCHEDULE is "other" to Jev)."""
from bson import ObjectId
from fastapi.testclient import TestClient

import extract.api as extract_api
from app.main import app

COVER = "COVER PAGE, DRAWING LIST, ASSEMBLY SCHEDULE"


class _Block:
    def __init__(self, tag):
        self.tag = tag


def _sheet(db, job, **over):
    return db.sheets.insert_one({"job_id": job, "page": 1, "kind": "other", "file": "a.pdf", "file_page": 1, **over}).inserted_id


def _stub_reader(monkeypatch, seen):
    monkeypatch.setattr(extract_api, "read_sheet_page", lambda path, n: seen.append((path.name, n)) or "page")
    monkeypatch.setattr(extract_api, "partition_blocks", lambda page: [_Block("A1"), _Block("A2")])
    monkeypatch.setattr(extract_api, "parse_blocks", lambda *a, **k: None)


def test_scan_picks_up_a_sheet_whose_title_names_the_assembly_schedule(mock_db, monkeypatch):
    monkeypatch.setenv("DB_NAME", mock_db.name)
    seen = []
    _stub_reader(monkeypatch, seen)
    job = str(mock_db.jobs.insert_one({"name": "x", "status": "ready"}).inserted_id)
    cover = _sheet(mock_db, job, title=COVER, sheet_no="G-001", file_page=3)
    _sheet(mock_db, job, page=2, title="LEVEL 1 FLOOR PLAN", kind="floor_plan", file_page=4)
    r = TestClient(app).post(f"/api/jobs/{job}/conditions/scan")
    assert r.status_code == 200, r.text
    assert [s["sheet_id"] for s in r.json()["sheets"]] == [str(cover)]
    assert seen == [("a.pdf", 3)], "only the cover page is read, not the floor plan"
    assert r.json()["tags"] == 2


def test_scan_still_picks_up_a_sheet_of_kind_partition_types(mock_db, monkeypatch):
    monkeypatch.setenv("DB_NAME", mock_db.name)
    _stub_reader(monkeypatch, [])
    job = str(mock_db.jobs.insert_one({"name": "x", "status": "ready"}).inserted_id)
    typed = _sheet(mock_db, job, kind="partition_types", title="A-501")
    r = TestClient(app).post(f"/api/jobs/{job}/conditions/scan")
    assert r.status_code == 200 and [s["sheet_id"] for s in r.json()["sheets"]] == [str(typed)]


def test_scan_422_sentences_point_at_something_the_ui_has(mock_db, monkeypatch):
    monkeypatch.setenv("DB_NAME", mock_db.name)
    job = str(mock_db.jobs.insert_one({"name": "x", "status": "ready"}).inserted_id)
    _sheet(mock_db, job, kind="floor_plan", title="LEVEL 1 FLOOR PLAN")
    none = TestClient(app).post(f"/api/jobs/{job}/conditions/scan")
    assert none.status_code == 422
    assert "partition" in none.json()["detail"].lower() and "New condition" in none.json()["detail"]
    assert "Parse this sheet" not in none.json()["detail"]

    monkeypatch.setattr(extract_api, "read_sheet_page", lambda path, n: "page")
    monkeypatch.setattr(extract_api, "partition_blocks", lambda page: [])
    _sheet(mock_db, job, page=2, kind="partition_types", title="A-501")
    empty = TestClient(app).post(f"/api/jobs/{job}/conditions/scan")
    assert empty.status_code == 422
    assert "partition" in empty.json()["detail"].lower() and "New condition" in empty.json()["detail"]
