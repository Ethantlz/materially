"""Offline checks for contracts/, setup and the router (mongomock, no network)."""
import json
from pathlib import Path

import pytest

from app import router
from app.router import TaskFailed, parse_json, run_task, system_prompt
from contracts.samples import BAD_CONDITION, SAMPLES
from contracts.schemas import VALIDATORS
from contracts.seed import MODEL_POLICY, price_book
from contracts.setup_db import setup
from contracts.validate import validate

GOOD = '{"type_tag": "A1", "studs": {"size_in": 3.625, "gauge": 20, "spacing_in": 16}, ' \
       '"layers_each_side": [{"thickness_in": 0.625, "board_type": "type_x"}], "sides": 2}'
BAD = '{"type_tag": "A1", "studs": {"size_in": 3.625, "gauge": 19, "spacing_in": 16}}'


@pytest.fixture
def db(mock_db):
    return mock_db


def test_one_valid_sample_per_collection():
    assert set(SAMPLES) == set(VALIDATORS)
    for name, doc in SAMPLES.items():
        assert validate(name, doc) == [], name


def test_bad_condition_rejected():
    errs = validate("conditions", BAD_CONDITION)
    assert any("gauge" in e for e in errs) and any("layers_each_side" in e for e in errs)


def test_ai_mode_skips_caller_fields():
    draft = {"line_items": [{"desc": "Studs", "qty": 5, "unit": "ea", "unit_cost": 10.15}], "total": 50.75}
    assert validate("ewo_drafts", draft, ai=True) == []
    assert validate("ewo_drafts", draft) != []  # ewo_id and task are required in the collection


def test_json_number_types():
    cond = dict(SAMPLES["conditions"], studs={"size_in": 6, "gauge": 20.0, "spacing_in": 16})
    assert validate("conditions", cond) == []
    cond["studs"]["gauge"] = "20"
    assert validate("conditions", cond) != []


def test_seed_rows_valid_and_sample_order_items_priced():
    assert all(validate("model_policy", p) == [] for p in MODEL_POLICY)
    rows = price_book()
    assert all(validate("price_book", r) == [] for r in rows)
    items = {r["item"] for r in rows}
    assert len(items) == len(rows)
    assert {line["item"] for line in SAMPLES["orders"]["lines"]} <= items


EXPECTED = sorted((Path(__file__).resolve().parents[2] / "fixtures" / "expected").glob("*.json"))


@pytest.mark.parametrize("path", EXPECTED, ids=[p.name for p in EXPECTED])
def test_expected_labels_match_contract(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if path.stem.endswith("_sheets"):
        from contracts.schemas import SHEET_KINDS

        assert all(s["kind"] in SHEET_KINDS and s["page"] >= 1 for s in data["sheets"])
    else:
        for cond in data["conditions"]:
            assert validate("conditions", cond, ai=True) == [], cond.get("type_tag")


def test_setup_is_idempotent(db):
    setup(db)
    assert db.model_policy.count_documents({}) == len(MODEL_POLICY)
    assert db.price_book.count_documents({}) == len(price_book())


@pytest.mark.parametrize("text", ['{"a": 1}', '```json\n{"a": 1}\n```', '<think>x {"b": 2}</think>Here: {"a": 1} done'])
def test_parse_json(text):
    assert parse_json(text) == {"a": 1}


def test_system_prompt_carries_schema():
    p = system_prompt("parse_partition", {}, "conditions")
    assert "type_tag" in p and "job_id" not in p


def _fake(replies: dict[str, list[str]]):
    def call(model, system, user):
        return replies[model].pop(0), 100, 20
    return call


def test_run_task_cheap_model_passes(db, monkeypatch):
    monkeypatch.setattr(router, "call_model", _fake({"deepseek-ai/DeepSeek-V4.1-Flash": [GOOD]}))
    out = run_task("parse_partition", "A1 ...", "conditions", job_id="j1", db=db)
    assert out["escalated"] is False and out["result"]["type_tag"] == "A1"
    assert db.usage.count_documents({"job_id": "j1", "ok": True}) == 1


def test_run_task_retries_on_retry_model(db, monkeypatch):
    monkeypatch.setattr(router, "call_model", _fake({"deepseek-ai/DeepSeek-V4.1-Flash": [BAD], "claude-sonnet-5-5": [GOOD]}))
    out = run_task("parse_partition", "A1 ...", "conditions", job_id="j2", db=db)
    assert out == {"result": parse_json(GOOD), "model": "claude-sonnet-5-5", "escalated": True}
    rows = list(db.usage.find({"job_id": "j2"}).sort("at", 1))
    assert [(r["model"], r["ok"]) for r in rows] == [("deepseek-ai/DeepSeek-V4.1-Flash", False), ("claude-sonnet-5-5", True)]


def test_run_task_raises_when_both_fail(db, monkeypatch):
    monkeypatch.setattr(router, "call_model", _fake({"deepseek-ai/DeepSeek-V4.1-Flash": [BAD], "claude-sonnet-5-5": ["nope"]}))
    with pytest.raises(TaskFailed) as e:
        run_task("parse_partition", "A1 ...", "conditions", db=db)
    assert "JSON" in e.value.reason
    # the last answer that parsed rides along, so the caller can let MongoDB's validator reject it
    assert e.value.result == parse_json(BAD) and e.value.model == "deepseek-ai/DeepSeek-V4.1-Flash"


def test_policy_params_reach_the_primary_model_only(db, monkeypatch):
    seen = []

    def call(model, system, user, params=None):
        seen.append((model, params))
        return (BAD if model.startswith("deepseek") else GOOD), 100, 20

    monkeypatch.setattr(router, "call_model", call)
    params = db.model_policy.find_one({"task": "price_extra"})["params"]
    assert params["extra_body"]["chat_template_kwargs"]["thinking"] is False and params["timeout_s"] == 30
    db.model_policy.update_one({"task": "parse_partition"}, {"$set": {"params": {"max_tokens": 900}}})
    run_task("parse_partition", "A1 ...", "conditions", db=db)
    assert seen == [("deepseek-ai/DeepSeek-V4.1-Flash", {"max_tokens": 900}), ("claude-sonnet-5-5", None)]


def test_no_retry_when_the_drawing_just_doesnt_say(db, monkeypatch):
    """SB2-style 'STUD SIZE: SEE UL DET.': a retry could only invent a size, so MongoDB gets the null to reject."""
    calls = []
    sb2 = ('{"type_tag": "SB2", "studs": {"size_in": null, "gauge": null, "spacing_in": 16}, '
           '"layers_each_side": [{"thickness_in": 0.625, "board_type": "type_x"}], "sides": null}')

    def call(model, system, user, params=None):
        calls.append(model)
        return sb2, 100, 20

    monkeypatch.setattr(router, "call_model", call)
    with pytest.raises(TaskFailed) as e:
        run_task("parse_partition", "SB2 ...", "conditions", db=db)
    assert calls == ["deepseek-ai/DeepSeek-V4.1-Flash"]
    assert e.value.result["studs"]["size_in"] is None and e.value.model == "deepseek-ai/DeepSeek-V4.1-Flash"


def test_task_failed_carries_latest_invalid_answer(db, monkeypatch):
    monkeypatch.setattr(router, "call_model", _fake({"deepseek-ai/DeepSeek-V4.1-Flash": [BAD], "claude-sonnet-5-5": [BAD]}))
    with pytest.raises(TaskFailed) as e:
        run_task("parse_partition", "A1 ...", "conditions", db=db)
    assert e.value.result == parse_json(BAD) and e.value.model == "claude-sonnet-5-5"
