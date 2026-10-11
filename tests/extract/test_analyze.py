"""After pass 1: Jev sorts each sheet, partition sheets become conditions, floor plans become rooms.

Model calls are faked here (jev.choice, router.run_task); tests/extract/test_live.py makes the real ones.
"""
import threading
import time

import pytest

from app import jev, router
from app.db import now, oid
from contracts.validate import validate
from extract import analyze
from extract.ingest import ingest, name_sheets
from extract.sheetinfo import guess_kind
from tests.extract.helpers import forget_job

STUDS = {"size_in": 3.625, "gauge": 20, "spacing_in": 16}
LAYERS = [{"thickness_in": 0.625, "board_type": "type_x"}]


def agreed(kind: str) -> dict:
    return {
        "choice": kind, "agreed": True, "probabilities": {kind: 0.9}, "model": "typesafe/jev-1.13", "ms": 5.0,
        "usage": {"input_tokens": 10, "output_tokens": 2, "cost_usd": 0.0}, "answers": [{"choice": kind}] * 2,
    }


def keyword_jev(state, options, question, **kw):
    """A Jev that agrees with the title keywords."""
    return agreed(guess_kind(state))


def parsed(task, payload, validator_name=None, **kw):
    """A parse_partition that reads the tag off the block's first line."""
    return {"result": {"type_tag": payload.splitlines()[0], "studs": STUDS, "layers_each_side": LAYERS, "sides": 2},
            "model": "deepseek-ai/DeepSeek-V4.1-Flash", "escalated": False}


class Peak:
    """Wraps a fake model call and records the most calls in flight at once."""

    def __init__(self, fn, seconds=0.05):
        self.fn, self.seconds, self.now, self.peak, self.lock = fn, seconds, 0, 0, threading.Lock()

    def __call__(self, *a, **kw):
        with self.lock:
            self.now += 1
            self.peak = max(self.peak, self.now)
        time.sleep(self.seconds)
        with self.lock:
            self.now -= 1
        return self.fn(*a, **kw)


@pytest.fixture
def job(mock_db):
    return str(mock_db.jobs.insert_one({"name": "t", "created_at": now(), "status": "processing"}).inserted_id)


@pytest.fixture
def named(mock_db, job, synthetic_pdf):
    """sheet number -> (sheet doc, PageText) after pass 1."""
    return {sheet["sheet_no"]: (sheet, page) for sheet, page in name_sheets(mock_db, job, [synthetic_pdf])}


def test_agreed_choice_sets_kind_and_routing(mock_db, job, named, monkeypatch):
    seen = []
    monkeypatch.setattr(jev, "choice", lambda state, *a, **kw: seen.append(state) or agreed("details"))
    sheet, page = named["A-501"]
    assert analyze.sort_sheet(mock_db, job, sheet) == "details"
    doc = mock_db.sheets.find_one({"_id": sheet["_id"]})
    assert doc["kind"] == "details"
    assert doc["routing"]["method"] == "jev" and doc["routing"]["choice"] == "details" and doc["routing"]["agreed"]
    assert mock_db.review_queue.count_documents({}) == 0
    assert "PARTITION TYPES" in seen[0] and '1 1/2" = 1\'-0"' in seen[0]
    assert "SAMPLE ARCHITECTS" not in seen[0], "boilerplate repeated on every sheet says nothing about the kind"


def test_disagreement_keeps_keyword_kind_and_queues_review(mock_db, job, named, monkeypatch):
    split = {**agreed("details"), "choice": None, "agreed": False, "answers": [{"choice": "details"}, {"choice": "other"}]}
    monkeypatch.setattr(jev, "choice", lambda *a, **kw: split)
    sheet, page = named["A-501"]
    assert analyze.sort_sheet(mock_db, job, sheet) == "partition_types"
    doc = mock_db.sheets.find_one({"_id": sheet["_id"]})
    assert doc["kind"] == "partition_types" and doc["routing"]["agreed"] is False
    review = mock_db.review_queue.find_one({"source": "extract.sheet_kind"})
    assert review["doc"]["sheet_id"] == str(sheet["_id"]) and "disagree" in review["reason"]


def test_jev_error_keeps_keyword_kind_and_queues_review(mock_db, job, named, monkeypatch):
    def down(*a, **kw):
        raise jev.JevError("all System One models failed")

    monkeypatch.setattr(jev, "choice", down)
    sheet, page = named["A-101"]
    assert analyze.sort_sheet(mock_db, job, sheet) == "floor_plan"
    doc = mock_db.sheets.find_one({"_id": sheet["_id"]})
    assert doc["kind"] == "floor_plan" and doc["routing"]["method"] == "title_keywords"
    assert "System One" in doc["routing"]["error"]
    assert "System One" in mock_db.review_queue.find_one({"source": "extract.sheet_kind"})["reason"]


def test_at_most_8_jev_calls_at_once(mock_db, job, named, monkeypatch):
    fake = Peak(lambda *a, **kw: agreed("other"))
    monkeypatch.setattr(jev, "choice", fake)
    analyze.analyze(mock_db, job, list(named.values()) * 3)
    assert fake.peak == 8


def test_partition_sheet_becomes_conditions_and_plans_become_rooms(mock_db, job, named, monkeypatch):
    monkeypatch.setattr(jev, "choice", keyword_jev)
    monkeypatch.setattr(router, "run_task", parsed)
    analyze.analyze(mock_db, job, list(named.values()))

    a501 = named["A-501"][0]
    conditions = list(mock_db.conditions.find({"job_id": job}).sort("type_tag", 1))
    assert [c["type_tag"] for c in conditions] == ["A1", "A2", "B1"]
    for c in conditions:
        assert validate("conditions", c) == []
        assert c["source"]["sheet"] == str(a501["_id"]) and c["source"]["page"] == a501["page"]
        assert len(c["source"]["bbox"]) == 4
        assert c["routing"]["model"] == "deepseek-ai/DeepSeek-V4.1-Flash" and c["routing"]["escalated"] is False

    rooms = list(mock_db.rooms.find({"job_id": job}))
    by_sheet = {}
    for r in rooms:
        assert validate("rooms", r) == []
        by_sheet.setdefault(r["sheet"], []).append(r["number"])
    plans = [str(named[no][0]["_id"]) for no in ("A-101", "A-102")]
    assert {k: sorted(v) for k, v in by_sheet.items()} == {sid: ["101", "102", "103"] for sid in plans}
    assert mock_db.jobs.find_one({"_id": oid(job)})["pages_sorted"] == 7


def test_failed_parse_goes_to_review(mock_db, job, named, monkeypatch):
    def flaky(task, payload, validator_name=None, **kw):
        if payload.startswith("B1"):
            raise router.TaskFailed(task, "studs.gauge: 19 not one of [12, 14, 16, 18, 20, 22, 25, 30]", "{}")
        return parsed(task, payload)

    monkeypatch.setattr(router, "run_task", flaky)
    sheet, page = named["A-501"]
    analyze.parse_sheet(mock_db, job, sheet, page)
    assert sorted(c["type_tag"] for c in mock_db.conditions.find({"job_id": job})) == ["A1", "A2"]
    review = mock_db.review_queue.find_one({"source": "extract.partition"})
    assert "gauge" in review["reason"] and review["doc"]["type_tag"] == "B1" and review["doc"]["text"].startswith("B1")


def test_at_most_4_run_tasks_at_once(mock_db, job, named, monkeypatch):
    fake = Peak(parsed)
    monkeypatch.setattr(jev, "choice", keyword_jev)
    monkeypatch.setattr(router, "run_task", fake)
    analyze.analyze(mock_db, job, [named["A-501"]] * 3)  # 9 blocks
    assert fake.peak == 4


def test_names_are_written_before_jev_is_asked(mock_db, job, synthetic_pdf, tmp_path, monkeypatch):
    named_at_first_ask = []

    def first(state, *a, **kw):
        if not named_at_first_ask:
            named_at_first_ask.append(mock_db.sheets.count_documents({"job_id": job, "sheet_no": {"$ne": None}}))
        return keyword_jev(state)

    monkeypatch.setattr(jev, "choice", first)
    monkeypatch.setattr(router, "run_task", parsed)
    ingest(mock_db, job, [synthetic_pdf], tmp_path)
    assert named_at_first_ask == [7]
    assert mock_db.jobs.find_one({"_id": oid(job)})["status"] == "ready"
    assert mock_db.conditions.count_documents({"job_id": job}) == 3


def test_mongodb_rejection_lands_in_review_queue(live_db, synthetic_pdf, monkeypatch):
    """The demo beat: a condition MongoDB's validator rejects is parked in review_queue with the reason."""
    db = live_db
    job_id = str(db.jobs.insert_one({"name": "rejection test", "created_at": now(), "status": "processing"}).inserted_id)
    try:
        sheet, page = next((s, p) for s, p in name_sheets(db, job_id, [synthetic_pdf]) if s["sheet_no"] == "A-501")
        bad = {"type_tag": "B4", "studs": {**STUDS, "gauge": 19}, "layers_each_side": LAYERS}  # as if a model skipped validation
        monkeypatch.setattr(router, "run_task", lambda *a, **kw: {"result": bad, "model": "m", "escalated": False})
        analyze.parse_sheet(db, job_id, sheet, page)
        assert db.conditions.count_documents({"job_id": job_id}) == 0
        reviews = list(db.review_queue.find({"source": "extract.partition", "doc.job_id": job_id}))
        assert len(reviews) == 3 and all("validation" in r["reason"].lower() for r in reviews)
        assert reviews[0]["collection"] == "conditions"
    finally:
        forget_job(db, job_id)


def test_failed_parse_with_an_answer_is_rejected_by_mongodb(live_db, synthetic_pdf, monkeypatch):
    """TaskFailed carrying the model's last (invalid) answer: MongoDB itself rejects it into review_queue."""
    db = live_db
    job_id = str(db.jobs.insert_one({"name": "taskfailed test", "created_at": now(), "status": "processing"}).inserted_id)
    try:
        sheet, page = next((s, p) for s, p in name_sheets(db, job_id, [synthetic_pdf]) if s["sheet_no"] == "A-501")
        bad = {"type_tag": "A1", "studs": {"size_in": None, "gauge": None, "spacing_in": None}, "layers_each_side": LAYERS}

        def fails(task, payload, *a, **kw):
            raise router.TaskFailed(task, "studs.size_in: expected number", "{}", result=bad, model="claude-sonnet-5-5")

        monkeypatch.setattr(router, "run_task", fails)
        analyze.parse_sheet(db, job_id, sheet, page)
        assert db.conditions.count_documents({"job_id": job_id}) == 0
        reviews = list(db.review_queue.find({"source": "extract.partition", "doc.job_id": job_id}))
        assert len(reviews) == 3 and all("Document failed validation" in r["reason"] for r in reviews)
        assert reviews[0]["doc"]["routing"] == {"task": "parse_partition", "model": "claude-sonnet-5-5", "escalated": True}
        assert reviews[0]["doc"]["source"]["sheet"] == str(sheet["_id"])
    finally:
        forget_job(db, job_id)
