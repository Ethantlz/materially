"""The Suppliers popup: the supplier sites product searches are limited to, and how the popup is opened.

The panel reads the `product_sources` collection directly (procurement's API is what writes it), so these tests
seed that collection themselves — the popup works before the other lane's routes exist.
"""
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.main import app
from contracts.samples import JOB_ID, SAMPLES

ROOT = Path(__file__).resolve().parents[2]
SITE = {
    "_id": ObjectId(),
    "url": "https://www.usg.com/ceilings",
    "domain": "usg.com",
    "name": "USG",
    "created_at": datetime(2026, 10, 11, 16, 0, tzinfo=timezone.utc),
}


@pytest.fixture
def client(mock_db, monkeypatch):
    """The same seeding as tests/ui/test_pages.py's client fixture (the takeoff page needs its job and sheets)."""
    monkeypatch.setenv("DB_NAME", mock_db.name)
    for name, doc in SAMPLES.items():
        if name not in ("price_book", "model_policy", "product_sources"):  # seeded already / each test picks its own
            mock_db[name].insert_one(deepcopy(doc))
    return TestClient(app)


def test_panel_lists_a_supplier_site(client, mock_db):
    mock_db.product_sources.insert_one(deepcopy(SITE))
    r = client.get("/ui/panel/sources")
    assert r.status_code == 200
    page = r.text
    assert 'data-testid="sources-panel"' in page and 'data-testid="source-add"' in page
    assert 'data-testid="sources-empty"' not in page
    row = page[page.index('data-testid="source-row"'):]
    row = row[: row.index("</tr>")]
    assert "<b>usg.com</b>" in row and "USG" in row  # the domain is the headline, the name sits beside it
    assert 'href="https://www.usg.com/ceilings"' in row and 'target="_blank"' in row and 'rel="noopener"' in row
    assert f'data-source-del="{SITE["_id"]}"' in row


def test_panel_says_so_when_no_supplier_is_listed(client):
    r = client.get("/ui/panel/sources")
    assert r.status_code == 200
    page = r.text
    assert 'data-testid="sources-empty"' in page and "whole web" in page
    assert 'data-testid="source-row"' not in page
    assert 'data-testid="source-url"' in page and 'name="url"' in page  # the add form is always there


def test_takeoff_has_the_suppliers_button(client):
    page = client.get(f"/jobs/{JOB_ID}/takeoff").text
    button = page[page.index('data-testid="open-sources"'):]
    assert button[: button.index("</button>")].startswith('data-testid="open-sources">Suppliers')
    assert 'id="open-sources"' in page


def test_modals_js_opens_the_suppliers_popup():
    js = (ROOT / "ui" / "static" / "modals.js").read_text(encoding="utf-8")
    assert re.search(r'bind\(\s*"open-sources",\s*"Suppliers",\s*\(\)\s*=>\s*"/ui/panel/sources"\s*\)', js)


def test_the_panel_script_speaks_the_product_sources_contract():
    """The panel is the only place these routes are called from, so the wiring is checked where it lives."""
    html = (ROOT / "ui" / "templates" / "_sources.html").read_text(encoding="utf-8")
    assert 'call("POST", ["/api/product-sources"]' in html
    assert 'call("DELETE", ["/api/product-sources/" + ' in html
    assert 'toast(errText(r, "Couldn\'t add that supplier"), true)' in html
    assert 'Modal.open("Suppliers", "/ui/panel/sources")' in html
