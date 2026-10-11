"""Each screen renders from the contracts' sample docs, and every testid in contracts/TESTIDS.md exists on its page."""
import html
import re
from copy import deepcopy
from pathlib import Path

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.main import app
from contracts.samples import CONDITION_ID, DRAFT_ID, JOB_ID, ORDER_ID, SAMPLES, SHEET_PT_ID
from ui.api import inches

ROOT = Path(__file__).resolve().parents[2]
EMPTY_JOB = ObjectId()
SENT_ORDER = ObjectId()


@pytest.fixture
def client(mock_db, monkeypatch):
    monkeypatch.setenv("DB_NAME", mock_db.name)
    for name, doc in SAMPLES.items():
        if name not in ("price_book", "model_policy"):  # mock_db is already seeded with these
            mock_db[name].insert_one(deepcopy(doc))
    mock_db.conditions.update_one({"_id": CONDITION_ID}, {"$set": {"routing.escalated": True}})
    mock_db.ewo_drafts.update_one({"_id": DRAFT_ID}, {"$set": {"escalated": True, "job_id": str(JOB_ID)}})
    mock_db.orders.update_one({"_id": ORDER_ID}, {"$set": {"guard": {"over": True, "items_over": ["board_0.625_type_x_4x10"]}}})
    sent = deepcopy(SAMPLES["orders"]) | {"_id": SENT_ORDER, "sent_at": SAMPLES["jobs"]["created_at"], "guard": {"over": False, "items_over": []}}
    mock_db.orders.insert_one(sent)
    mock_db.review_queue.insert_one({
        "source": "extract.partition", "collection": "conditions", "at": SAMPLES["jobs"]["created_at"],
        "doc": {"job_id": str(JOB_ID), "type_tag": "B4", "studs": {"size_in": 3.625, "gauge": 19, "spacing_in": 16}},
        "reason": "Document failed validation: {'details': [{'propertyName': 'gauge', 'reason': 'value was not found in enum', 'consideredValue': 19}]}",
    })
    mock_db.review_queue.insert_one({  # a parse that failed before any write has no `collection`
        "source": "extract.partition", "at": SAMPLES["jobs"]["created_at"], "reason": "studs.size_in: expected number, got NoneType None",
        "doc": {"job_id": str(JOB_ID), "sheet": str(SHEET_PT_ID), "type_tag": "SB2"},
    })
    mock_db.jobs.insert_one({"_id": EMPTY_JOB, "name": "Empty", "status": "ready", "created_at": SAMPLES["jobs"]["created_at"]})
    return TestClient(app)


def _pages(client) -> dict[str, list[str]]:
    """Each TESTIDS page pattern -> rendered HTML in the full state and the empty state."""
    ids = {"full": {"job": JOB_ID, "condition": CONDITION_ID, "order": ORDER_ID},
           "empty": {"job": EMPTY_JOB, "condition": CONDITION_ID, "order": SENT_ORDER}}
    out = {}
    for pattern in {line.split("|")[1].strip() for line in _testid_lines()}:
        htmls = []
        for state, vals in ids.items():
            url = pattern.format(**{k: str(v) for k, v in vals.items()})
            if pattern == "/review" and state == "empty":
                url += f"?job_id={EMPTY_JOB}"
            r = client.get(url)
            assert r.status_code == 200, (url, r.text[:300])
            htmls.append(r.text)
        out[pattern] = htmls
    return out


def _testid_lines() -> list[str]:
    text = (ROOT / "contracts" / "TESTIDS.md").read_text(encoding="utf-8")
    return [line[2:] for line in text.splitlines() if line.startswith("- ") and line.count("|") == 2]


def test_every_screen_renders(client):
    for url in (f"/jobs/{JOB_ID}/conditions", f"/conditions/{CONDITION_ID}", f"/jobs/{JOB_ID}/orders",
                f"/orders/{ORDER_ID}", f"/jobs/{JOB_ID}/ewo", f"/jobs/{JOB_ID}/pm", "/review", f"/jobs/{JOB_ID}", f"/jobs/{JOB_ID}/takeoff",
                f"/jobs/{EMPTY_JOB}/takeoff", "/"):
        r = client.get(url)
        assert r.status_code == 200, url


def test_sheet_select_has_names_not_scales(client):
    page = client.get(f"/jobs/{JOB_ID}/takeoff").text
    sel = page[page.index('data-testid="sheet-select"'):]
    sel = sel[: sel.index("</select>")]
    assert "A-601" in sel and "PARTITION TYPES" in sel
    assert "NTS" not in sel and "= 1'" not in sel  # no scale in the dropdown
    i_undo, i_scale = page.index('data-testid="tool-undo"'), page.index('data-testid="canvas-scale"')
    assert 0 < i_scale - i_undo < 400  # the scale chip sits right after Undo
    assert page.index('data-testid="cond-panel"') < page.index('data-testid="takeoff-canvas"')  # panel on the left
    for tid in ("alt-select", "scan-partitions", "open-orders", "open-ewos", "open-tracking", "open-review"):
        assert f'data-testid="{tid}"' in page


def test_job_url_redirects_to_takeoff(client):
    r = client.get(f"/jobs/{JOB_ID}", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"].endswith(f"/jobs/{JOB_ID}/takeoff")


def test_every_testid_exists_on_its_page(client):
    pages = _pages(client)
    js = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "ui" / "static").glob("*.js"))
    missing = []
    for line in _testid_lines():
        testid, page, what = (part.strip() for part in line.split("|"))
        found = any(f'data-testid="{testid}"' in page_html for page_html in pages[page])
        if "(js)" in what:  # data-testid="x" in a template string, el.dataset.testid = "x", or {"data-testid": "x"}
            found = found or re.search(r"""testid["']?\s*[:=]\s*["']""" + re.escape(testid) + r"""["']""", js)
        if not found:
            missing.append(f"{testid} on {page}")
    assert not missing, missing


def test_projects_page_lists_alternates_and_links_to_takeoff(client, mock_db):
    from projects import alternates as alt
    a1 = alt.create_alternate(str(JOB_ID), "Alternate 1")
    page = client.get("/").text
    assert 'data-testid="project-row"' in page and "Alternate 1" in page
    assert f'/jobs/{JOB_ID}/takeoff?alt={a1}' in page


def test_conditions_page_lists_both_kinds_of_rejected_parse(client):
    html = client.get(f"/jobs/{JOB_ID}/conditions").text
    assert html.count('data-testid="condition-rejected"') == 2
    assert "SB2" in html and "gauge: 19 not allowed" in html and "expected number" in html


def test_pages_show_the_key_numbers(client):
    order = client.get(f"/orders/{ORDER_ID}").text
    assert "Over-purchase guard tripped" in order and "$2,213.15" in order
    cond = html.unescape(client.get(f"/conditions/{CONDITION_ID}").text)
    assert '3-5/8"' in cond and "Type X" in cond and "U419" in cond
    pm = client.get(f"/jobs/{JOB_ID}/pm").text
    assert "$625.55" in pm and "escalated" in pm
    cost = client.get(f"/ui/jobs/{JOB_ID}/cost").json()
    assert cost["calls"] == 1 and cost["cost_usd"] == 0.00023
    assert cost["by_task"] == [{"task": "parse_partition", "model": "deepseek-ai/DeepSeek-V4.1-Flash", "calls": 1, "cost_usd": 0.00023}]
    assert client.get(f"/ui/jobs/{JOB_ID}/drafts").json() == [str(DRAFT_ID)]
    assert client.get(f"/ui/drafts/{DRAFT_ID}/card").status_code == 200


@pytest.mark.parametrize("value,text", [(3.625, '3-5/8"'), (0.625, '5/8"'), (6, '6"'), (2.5, '2-1/2"'), (None, "—")])
def test_inches(value, text):
    assert inches(value) == text


def test_reason_summary_reads_mongo_rejection():
    from ui.api import reason_summary

    raw = ("Document failed validation: {'operatorName': '$jsonSchema', 'schemaRulesNotSatisfied': [{'operatorName': "
           "'properties', 'propertiesNotSatisfied': [{'propertyName': 'studs', 'details': [{'operatorName': 'properties', "
           "'propertiesNotSatisfied': [{'propertyName': 'gauge', 'details': [{'operatorName': 'enum', 'specifiedAs': "
           "{'enum': [12, 14, 16, 18, 20, 22, 25, 30]}, 'reason': 'value was not found in enum', 'consideredValue': 19}]}]}]}]}, "
           "{'operatorName': 'required', 'specifiedAs': {'required': ['type_tag', 'studs', 'layers_each_side']}, "
           "'missingProperties': ['layers_each_side']}]}")
    assert reason_summary(raw) == "Rejected by MongoDB's validator · gauge: 19 not allowed · missing: layers_each_side"
    assert reason_summary("Jev's two answers disagreed") == "Jev's two answers disagreed"


def test_light_tokens_and_no_tab_nav(client):
    css = (ROOT / "ui" / "static" / "app.css").read_text(encoding="utf-8")
    for token in ("--ink: #111827", "--line: #e5e7eb", "--stage: #eef0f3", "--accent: #3b82f6"):
        assert token in css
    page = client.get(f"/jobs/{JOB_ID}/takeoff").text
    assert 'data-testid="nav-tabs"' not in page and 'data-testid="nav-home"' in page


def test_takeoff_js_renders_materials_and_scan():
    js = (ROOT / "ui" / "static" / "takeoff.js").read_text(encoding="utf-8")
    for tid in ("cond-materials", "material-row", "material-find", "material-product"):
        assert f'data-testid="{tid}"' in js
    assert "Takeoff.scan(" in js


def test_condition_views_pass_materials_through():
    from ui.common import condition_views

    mats = [{"key": "stud_0", "role": "stud", "desc": '3-5/8" 20ga stud @ 16" o.c.', "source": "derived", "product_id": None}]
    c = {"id": "c1", "type_tag": "A1", "studs": {"size_in": 3.625, "gauge": 20, "spacing_in": 16},
         "layers_each_side": [{"thickness_in": 0.625, "board_type": "type_x"}], "materials": mats}
    assert condition_views([c])[0]["materials"] == mats
    assert condition_views([{**c, "materials": None}])[0]["materials"] == []
