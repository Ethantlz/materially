import time
from concurrent.futures import ThreadPoolExecutor

from bson import ObjectId
from fastapi.testclient import TestClient

from app import router as model_router
from app.main import app
from extract import analyze
from extract.ingest import read_pages
from extract.partitions import partition_blocks
from scripts.make_drawing_set import build

GOOD = ('{"type_tag": "%s", "studs": {"size_in": 3.625, "gauge": 20, "spacing_in": 16}, '
        '"layers_each_side": [{"thickness_in": 0.625, "board_type": "type_x"}], "sides": 2}')


def _fake_model(monkeypatch):
    monkeypatch.setattr(model_router, "call_model", lambda model, system, user: (GOOD % user.split()[0], 10, 10))


def test_scan_without_partition_sheet_is_422(mock_db, monkeypatch):
    monkeypatch.setenv("DB_NAME", mock_db.name)
    job = str(mock_db.jobs.insert_one({"name": "x", "status": "ready"}).inserted_id)
    mock_db.sheets.insert_one({"job_id": job, "page": 1, "kind": "floor_plan", "file": "a.pdf", "file_page": 1})
    r = TestClient(app).post(f"/api/jobs/{job}/conditions/scan")
    assert r.status_code == 422 and "partition" in r.json()["detail"].lower()
    assert mock_db.conditions.count_documents({"job_id": job}) == 0


def test_rescan_keeps_condition_ids(mock_db, monkeypatch, tmp_path):
    monkeypatch.setenv("DB_NAME", mock_db.name)
    _fake_model(monkeypatch)
    pdf = tmp_path / "set.pdf"
    build(pdf)
    page = next(p for p in read_pages(pdf) if any(s.text.strip() == "A1" for s in p.segs))
    job = str(ObjectId())
    sheet = {"_id": ObjectId(), "job_id": job, "page": 6}
    first = analyze.parse_blocks(mock_db, job, sheet, partition_blocks(page))
    cid = str(mock_db.conditions.find_one({"type_tag": "A1"})["_id"])
    mock_db.measurements.insert_one({"job_id": job, "condition_id": cid, "quantity": 100, "unit": "lf"})
    second = analyze.parse_blocks(mock_db, job, sheet, partition_blocks(page))
    assert sorted(map(str, first)) == sorted(map(str, second))
    assert mock_db.conditions.count_documents({"job_id": job}) == 3
    assert mock_db.conditions.find_one({"_id": ObjectId(cid)})["materials"][0]["role"] == "stud"


def _job_sheet_blocks(tmp_path):
    """A job id, a made-up partition sheet doc and the A-501 type-tag blocks."""
    pdf = tmp_path / "set.pdf"
    build(pdf)
    page = next(p for p in read_pages(pdf) if any(s.text.strip() == "A1" for s in p.segs))
    job = str(ObjectId())
    return job, {"_id": ObjectId(), "job_id": job, "page": 6}, partition_blocks(page)


def test_parallel_parses_of_the_same_tag_insert_one_condition(mock_db, monkeypatch, tmp_path):
    """A scan double-click (or a scan during the upload-time parse) must not duplicate a type tag: the
    find_one -> insert in parse_block is check-then-act, so it is serialised per (job_id, type_tag)."""
    monkeypatch.setenv("DB_NAME", mock_db.name)
    _fake_model(monkeypatch)
    job, sheet, blocks = _job_sheet_blocks(tmp_path)
    real_insert = analyze.insert_or_review

    def slow_insert(db, collection, doc, source):
        time.sleep(0.05)  # hold the check-then-act window open, so both threads get past find_one
        return real_insert(db, collection, doc, source)

    monkeypatch.setattr(analyze, "insert_or_review", slow_insert)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(analyze.parse_blocks, mock_db, job, sheet, blocks) for _ in range(2)]
        first, second = [f.result() for f in futures]
    assert sorted(map(str, first)) == sorted(map(str, second))
    assert mock_db.conditions.count_documents({"job_id": job}) == 3


def test_rescan_keeps_product_id_and_person_edits(mock_db, monkeypatch, tmp_path):
    """Task 6 writes materials[].product_id when it finds a product; a re-scan must not throw it away,
    and a name or colour a person edited must survive the re-parse."""
    monkeypatch.setenv("DB_NAME", mock_db.name)
    _fake_model(monkeypatch)
    job, sheet, blocks = _job_sheet_blocks(tmp_path)
    analyze.parse_blocks(mock_db, job, sheet, blocks)
    a1 = mock_db.conditions.find_one({"type_tag": "A1"})
    assert a1["materials"][0]["key"] == "stud"
    mock_db.conditions.update_one(
        {"_id": a1["_id"]},
        {"$set": {"name": "Party wall", "color": "#3b82f6", "materials.0.product_id": "prod_stud_1"}},
    )
    analyze.parse_blocks(mock_db, job, sheet, blocks)
    doc = mock_db.conditions.find_one({"_id": a1["_id"]})
    assert doc["name"] == "Party wall" and doc["color"] == "#3b82f6"
    kept = {m["key"]: m.get("product_id") for m in doc["materials"]}
    assert kept["stud"] == "prod_stud_1"
    assert kept["board_0.625_type_x"] is None, "a row with no product found stays unfound"
