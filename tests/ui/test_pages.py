"""Each screen renders from the contracts' sample docs, and every testid in contracts/TESTIDS.md exists on its page."""
import html
import re
from copy import deepcopy
from pathlib import Path

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.main import app
from contracts.samples import CONDITION_ID, DRAFT_ID, JOB_ID, ORDER_ID, SAMPLES
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
                f"/orders/{ORDER_ID}", f"/jobs/{JOB_ID}/ewo", f"/jobs/{JOB_ID}/pm", "/review", f"/jobs/{JOB_ID}"):
        r = client.get(url)
        assert r.status_code == 200, url


def test_every_testid_exists_on_its_page(client):
    pages = _pages(client)
    js = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "ui" / "static").glob("*.js"))
    missing = []
    for line in _testid_lines():
        testid, page, what = (part.strip() for part in line.split("|"))
        found = any(f'data-testid="{testid}"' in page_html for page_html in pages[page])
        if "(js)" in what:
            found = found or re.search(rf'testid\s*=\s*"{re.escape(testid)}"|data-testid="{re.escape(testid)}"', js)
        if not found:
            missing.append(f"{testid} on {page}")
    assert not missing, missing


def test_pages_show_the_key_numbers(client):
    order = client.get(f"/orders/{ORDER_ID}").text
    assert "Over-purchase guard tripped" in order and "$2,213.15" in order
    cond = html.unescape(client.get(f"/conditions/{CONDITION_ID}").text)
    assert '3-5/8"' in cond and "Type X" in cond and "U419" in cond
    pm = client.get(f"/jobs/{JOB_ID}/pm").text
    assert "$625.55" in pm and "escalated" in pm
    assert client.get(f"/ui/jobs/{JOB_ID}/cost").json() == {"calls": 1, "cost_usd": 0.00023}
    assert client.get(f"/ui/jobs/{JOB_ID}/drafts").json() == [str(DRAFT_ID)]
    assert client.get(f"/ui/drafts/{DRAFT_ID}/card").status_code == 200


@pytest.mark.parametrize("value,text", [(3.625, '3-5/8"'), (0.625, '5/8"'), (6, '6"'), (2.5, '2-1/2"'), (None, "—")])
def test_inches(value, text):
    assert inches(value) == text
