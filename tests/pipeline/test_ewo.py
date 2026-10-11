"""EWO create (pin -> room by $near) and route (Jev picks the task, run_task drafts it, validator checks)."""
import os
from types import SimpleNamespace

import pytest

from app import jev
from app.router import TaskFailed
from pipeline import ewo
from tests.pipeline.conftest import add_condition, by_id, new_id

RESULT = {  # what run_task hands back for a good draft
    "result": {
        "line_items": [
            {"desc": '3-5/8" 20 ga studs, 10 ft', "qty": 5, "unit": "ea", "unit_cost": 10.15},
            {"desc": "Framing and hanging labor", "qty": 6, "unit": "hr", "unit_cost": 85.0},
        ],
        "total": 560.75,
        "explanation": "New 4 ft A1 wall at the alcove, not on the bid drawings; priced as an extra.",
    },
    "model": "deepseek-ai/DeepSeek-V4.1-Flash",
    "escalated": False,
}
ROUTING = {
    "choice": "price_extra",
    "agreed": True,
    "probabilities": {"price_extra": 0.91, "base_scope_check": 0.07, "backcharge": 0.02},
    "model": "typesafe/jev-1.13",
    "ms": 812.0,
    "usage": {"input_tokens": 600, "output_tokens": 40, "cost_usd": 0.0004},
    "answers": [{"choice": "price_extra"}, {"choice": "price_extra"}],
}
DESCRIPTION = "Owner added a 4 ft wall to close in the alcove, full height, same as A1."


def make_ewo(db, job, *, room_label=(1312.5, 884.0)):
    """A room on a sheet and an EWO pinned beside it; returns (ewo_id, room_id)."""
    sheet = new_id()
    room_id = str(db.rooms.insert_one({"job_id": job, "sheet": sheet, "name": "EXAM", "number": "104", "label_pt": list(room_label)}).inserted_id)
    return ewo.create(job, sheet, room_label[0] - 22, room_label[1] + 18, DESCRIPTION), room_id


@pytest.fixture
def models(monkeypatch):
    """Jev and run_task replaced by recorders; each test can overwrite .routing / .out / .error."""

    m = SimpleNamespace(routing=ROUTING, out=RESULT, error=None, choice_calls=[], task_calls=[])

    def choice(state, options, question="", **kw):
        m.choice_calls.append({"state": state, "options": options, "question": question, **kw})
        if isinstance(m.routing, Exception):
            raise m.routing
        return m.routing

    def run_task(task, payload, validator=None, **kw):
        m.task_calls.append({"task": task, "payload": payload, "validator": validator, **kw})
        if m.error:
            raise m.error
        return m.out

    monkeypatch.setattr(jev, "choice", choice)
    monkeypatch.setattr(ewo, "run_task", run_task)
    return m


@pytest.fixture
def policy(db):
    """Edit model_policy rows for a test and put them back (the real lane db is shared with other runs)."""
    saved = {}

    def enable(task, on):
        row = db.model_policy.find_one({"task": task})
        saved.setdefault(task, row["enabled"])
        db.model_policy.update_one({"task": task}, {"$set": {"enabled": on}})

    yield enable
    for task, was in saved.items():
        db.model_policy.update_one({"task": task}, {"$set": {"enabled": was}})


# --- create -----------------------------------------------------------------------------------------------------

def test_pin_near_room_label_returns_that_room(db, job):
    sheet, other_sheet = new_id(), new_id()
    near = db.rooms.insert_one({"job_id": job, "sheet": sheet, "name": "EXAM", "number": "104", "label_pt": [1312.5, 884.0]}).inserted_id
    db.rooms.insert_one({"job_id": job, "sheet": sheet, "name": "WAITING", "number": "101", "label_pt": [2500.0, 1800.0]})
    db.rooms.insert_one({"job_id": job, "sheet": other_sheet, "name": "OTHER SHEET", "number": "999", "label_pt": [1300.0, 890.0]})

    ewo_id = ewo.create(job, sheet, 1290.0, 902.0, DESCRIPTION)

    doc = by_id(db.ewos, ewo_id)
    assert doc["room_id"] == str(near)  # not the nearer label that sits on another sheet
    assert (doc["job_id"], doc["sheet"], doc["pin"], doc["status"], doc["submitted_by"]) == (job, sheet, [1290.0, 902.0], "new", "foreman, synthetic")
    assert doc["description"] == DESCRIPTION and doc["created_at"]


def test_far_pin_returns_no_room(db, job):
    sheet = new_id()
    db.rooms.insert_one({"job_id": job, "sheet": sheet, "name": "EXAM", "number": "104", "label_pt": [1312.5, 884.0]})
    for x, y in ((4000.0, 4000.0), (1312.5 + 410, 884.0)):  # nowhere near; just past the 400 pt limit
        doc = by_id(db.ewos, ewo.create(job, sheet, x, y, DESCRIPTION))
        assert doc["room_id"] is None
    inside = ewo.create(job, sheet, 1312.5 + 390, 884.0, DESCRIPTION)
    assert by_id(db.ewos, inside)["room_id"] is not None


def test_create_rejects_a_pin_off_the_sheet_and_an_empty_description(db, job):
    sheet = new_id()
    with pytest.raises(ValueError, match="pin"):
        ewo.create(job, sheet, 6000.0, 10.0, DESCRIPTION)
    with pytest.raises(ValueError, match="description"):
        ewo.create(job, sheet, 10.0, 10.0, "")
    assert db.ewos.count_documents({"job_id": job}) == 0


# --- route ------------------------------------------------------------------------------------------------------

def test_route_drafts_with_jev_choice_and_job_context(db, job, models):
    add_condition(db, job)
    ewo_id, room_id = make_ewo(db, job)

    draft_id = ewo.route(ewo_id)

    draft = by_id(db.ewo_drafts, draft_id)
    assert (draft["ewo_id"], draft["job_id"], draft["task"], draft["model"], draft["escalated"]) == (
        ewo_id, job, "price_extra", RESULT["model"], False)
    assert draft["routing"] == ROUTING and draft["total"] == 560.75 and len(draft["line_items"]) == 2
    assert by_id(db.ewos, ewo_id)["status"] == "drafted"

    (asked,) = models.choice_calls  # Jev saw the description and room, the three tasks, and the job
    assert asked["state"]["extra_work_order"] == DESCRIPTION and asked["state"]["room"] == {"name": "EXAM", "number": "104"}
    assert set(asked["options"]) == {"price_extra", "base_scope_check", "backcharge"} and all(asked["options"].values())
    assert asked["question"] == "Which task handles this extra work order?" and asked["job_id"] == job

    (ran,) = models.task_calls  # the draft model saw the EWO, the room, the job's walls and the prices they use
    assert (ran["task"], ran["validator"], ran["job_id"]) == ("price_extra", "ewo_drafts", job)
    payload = ran["payload"]
    assert payload["ewo"]["description"] == DESCRIPTION and payload["room"] == {"name": "EXAM", "number": "104"}
    (cond,) = payload["conditions"]
    assert cond["type_tag"] == "A1" and cond["studs"]["gauge"] == 20 and cond["fire_rating"] == 1 and cond["layers_each_side"]
    items = {p["item"]: p for p in payload["price_book"]}
    assert items["stud_3.625_20ga_10ft"]["unit_cost"] == 10.15 and "board_0.625_type_x_4x10" in items and "labor_framing_hr" in items
    assert "stud_6_16ga_10ft" not in items  # only items this job's walls (and the generics) can use


def test_route_recomputes_a_wrong_model_total(db, job, models):
    add_condition(db, job)
    models.out = {**RESULT, "result": {**RESULT["result"], "total": 999.0}}
    ewo_id, _ = make_ewo(db, job)

    draft = by_id(db.ewo_drafts, ewo.route(ewo_id))

    assert draft["total"] == 560.75 and draft["total_model"] == 999.0


def test_disabled_task_is_not_offered_to_jev(db, job, models, policy):
    policy("backcharge", False)
    ewo_id, _ = make_ewo(db, job)

    assert ewo.route(ewo_id)

    assert set(models.choice_calls[0]["options"]) == {"price_extra", "base_scope_check"}


def test_a_single_enabled_task_skips_the_vote(db, job, models, policy):
    policy("base_scope_check", False)
    policy("backcharge", False)
    ewo_id, _ = make_ewo(db, job)

    draft = by_id(db.ewo_drafts, ewo.route(ewo_id))

    assert models.choice_calls == [] and draft["task"] == "price_extra" and draft["routing"]["agreed"]


def test_no_enabled_task_goes_to_review(db, job, models, policy):
    for task in ewo.TASKS:
        policy(task, False)
    ewo_id, _ = make_ewo(db, job)

    assert ewo.route(ewo_id) is None

    row = db.review_queue.find_one({"doc.ewo_id": ewo_id})
    assert "enabled" in row["reason"] and row["source"] == "ewo_route"
    assert by_id(db.ewos, ewo_id)["status"] == "review"


def test_jev_disagreement_goes_to_review(db, job, models):
    models.routing = {**ROUTING, "choice": None, "agreed": False}
    ewo_id, _ = make_ewo(db, job)

    assert ewo.route(ewo_id) is None

    row = db.review_queue.find_one({"doc.ewo_id": ewo_id})
    assert row["source"] == "ewo_route" and "disagree" in row["reason"] and row["doc"]["job_id"] == job
    assert by_id(db.ewos, ewo_id)["status"] == "review"
    assert models.task_calls == [] and db.ewo_drafts.count_documents({"job_id": job}) == 0


def test_jev_down_goes_to_review(db, job, models):
    models.routing = jev.JevError("all System One models failed")
    ewo_id, _ = make_ewo(db, job)

    assert ewo.route(ewo_id) is None

    assert "Jev unavailable" in db.review_queue.find_one({"doc.ewo_id": ewo_id})["reason"]


def test_task_failure_goes_to_review(db, job, models):
    models.error = TaskFailed("price_extra", "line_items: required", raw="{}")
    ewo_id, _ = make_ewo(db, job)

    assert ewo.route(ewo_id) is None

    row = db.review_queue.find_one({"doc.ewo_id": ewo_id})
    assert row["source"] == "price_extra" and row["reason"] == "line_items: required" and row["doc"]["routing"]["choice"] == "price_extra"
    assert by_id(db.ewos, ewo_id)["status"] == "review"


def test_route_unknown_ewo(db):
    with pytest.raises(LookupError):
        ewo.route(new_id())


def test_invalid_draft_lands_in_review_queue_with_mongodbs_reason(live_db, live_job, models):
    bad = {"line_items": [{"desc": "studs", "qty": -3, "unit": "ea", "unit_cost": 10.15}], "total": 30.45, "explanation": "bad qty"}
    models.out = {**RESULT, "result": bad}
    ewo_id, _ = make_ewo(live_db, live_job)

    assert ewo.route(ewo_id) is None

    row = live_db.review_queue.find_one({"doc.ewo_id": ewo_id})
    assert row["source"] == "price_extra" and row["collection"] == "ewo_drafts" and row["doc"]["job_id"] == live_job
    assert row["reason"].startswith("Document failed validation")  # MongoDB's own errmsg and details
    assert "qty" in row["reason"]
    assert live_db.ewo_drafts.count_documents({"job_id": live_job}) == 0
    assert by_id(live_db.ewos, ewo_id)["status"] == "review"


@pytest.mark.skipif(not (os.environ.get("LITELLM_API_KEY") and os.environ.get("OPENROUTER_API_KEY")), reason="needs LITELLM_API_KEY and OPENROUTER_API_KEY (run under infisical)")
def test_routed_ewo_inserts_a_draft_live(live_db, live_job, client):
    """The real thing: Jev picks the task, DeepSeek drafts it, MongoDB validates it, usage rows are logged."""
    add_condition(live_db, live_job)
    ewo_id, _ = make_ewo(live_db, live_job)

    draft_id = ewo.route(ewo_id)

    reasons = [r["reason"] for r in live_db.review_queue.find({"doc.job_id": live_job})]
    assert draft_id, f"went to review_queue: {reasons}"
    draft = by_id(live_db.ewo_drafts, draft_id)
    assert draft["task"] in ewo.TASKS and draft["routing"]["agreed"] and draft["routing"]["choice"] == draft["task"]
    assert draft["job_id"] == live_job and draft["ewo_id"] == ewo_id and isinstance(draft["escalated"], bool)
    assert draft["total"] >= 0 and draft["explanation"]
    assert by_id(live_db.ewos, ewo_id)["status"] == "drafted"
    cost = client.get(f"/api/jobs/{live_job}/cost").json()
    assert cost["calls"] >= 3 and {t["task"] for t in cost["by_task"]} >= {"ewo_route", draft["task"]}
