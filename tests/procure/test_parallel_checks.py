"""A product's Jev spec checks (one per specified attribute) run concurrently, not one after another."""
import threading
import time

import pytest
from bson import ObjectId

from procure import find

# three specified attributes (thickness_in, board_type, gauge): three Jev checks
MATERIAL = {"key": "board_3", "role": "board", "desc": '5/8" Type X gypsum board', "thickness_in": 0.625, "board_type": "type_x",
            "gauge": 25, "per": "sf", "source": "drawing"}
PAGE = {"url": "https://example.com/firecode-x.pdf", "title": "5/8 Type X data sheet", "markdown": "Thickness 5/8 in. Type X."}
ATTRS = {"thickness_in": 0.625, "board_type": "type_x", "gauge": 25}
PAUSE = 0.25


@pytest.fixture
def cid(mock_db, monkeypatch):
    monkeypatch.setenv("DB_NAME", mock_db.name)
    return str(mock_db.conditions.insert_one({"type_tag": "SA4", "materials": [MATERIAL], "job_id": "j"}).inserted_id)


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(find, "_search", lambda q, domains=(): [PAGE])
    monkeypatch.setattr(find, "_attrs", lambda text, job_id: ATTRS)
    monkeypatch.setattr(find, "_key", lambda: "fc-test")


def test_three_checks_take_about_one_checks_time(cid, wired, monkeypatch):
    seen, lock = [], threading.Lock()

    def slow(material, attrs, attr, job_id):
        with lock:
            seen.append(attr)
        time.sleep(PAUSE)
        return {"thickness_in": 0.1, "board_type": 0.2, "gauge": 0.3}[attr]

    monkeypatch.setattr(find, "_p_mismatch", slow)
    t0 = time.perf_counter()
    doc = find.find_for_material(cid, MATERIAL["key"])
    took = time.perf_counter() - t0
    assert took < 0.6, f"three {PAUSE}s checks took {took:.2f}s: they ran one after another"  # sequential: >= 0.75 s
    assert sorted(seen) == ["board_type", "gauge", "thickness_in"]
    assert doc["checked"] is True and doc["spec_check"] == {"thickness_in": 0.1, "board_type": 0.2, "gauge": 0.3}
    assert list(doc["spec_check"]) == ["thickness_in", "board_type", "gauge"]  # CHECKED order, however they finish


def test_the_results_are_stored_rounded_and_the_product_linked(cid, mock_db, wired, monkeypatch):
    monkeypatch.setattr(find, "_p_mismatch", lambda material, attrs, attr, job_id: 0.123456)
    doc = find.find_for_material(cid, MATERIAL["key"])
    saved = mock_db.product_data.find_one({})
    assert saved["checked"] is True and saved["spec_check"] == {"thickness_in": 0.123, "board_type": 0.123, "gauge": 0.123}
    assert mock_db.conditions.find_one({"_id": ObjectId(cid)})["materials"][0]["product_id"] == doc["id"]


def test_one_failing_check_keeps_the_search_unchecked(cid, mock_db, wired, monkeypatch):
    def flaky(material, attrs, attr, job_id):
        time.sleep(0.05)
        if attr == "board_type":
            raise find.SearchFailed("Couldn't spec-check the product: Jev is down")
        return 0.05

    monkeypatch.setattr(find, "_p_mismatch", flaky)
    doc = find.find_for_material(cid, MATERIAL["key"])
    assert doc["url"] == PAGE["url"] and doc["checked"] is False and doc["spec_check"] == {}
    assert "Jev is down" in doc["check_error"]
    saved = mock_db.product_data.find_one({})
    assert mock_db.product_data.count_documents({}) == 1 and saved["checked"] is False and saved["spec_check"] == {}
    assert "check_error" not in saved and saved["attrs"] == ATTRS  # the paid search is kept
    assert mock_db.conditions.find_one({"_id": ObjectId(cid)})["materials"][0]["product_id"] == doc["id"]


def test_a_jev_error_in_one_check_is_wrapped_as_before(cid, mock_db, wired, monkeypatch):
    """The real _p_mismatch: a JevError on one attribute's question becomes the 'Couldn't spec-check' sentence."""
    def noul(_payload, question, task=None, job_id=None):
        time.sleep(0.05)
        if "gauge" in question:
            raise find.jev.JevError("all System One models failed")
        return {"p_yes": 0.05}

    monkeypatch.setattr(find.jev, "noul", noul)
    doc = find.find_for_material(cid, MATERIAL["key"])
    assert doc["checked"] is False and doc["spec_check"] == {}
    assert doc["check_error"] == "Couldn't spec-check the product: all System One models failed"
    assert mock_db.product_data.find_one({})["checked"] is False


def test_the_first_failure_in_check_order_is_the_one_reported(cid, wired, monkeypatch):
    def both_fail(material, attrs, attr, job_id):
        if attr == "thickness_in":
            time.sleep(0.15)  # fails last in time, but first in CHECKED order
        if attr in ("thickness_in", "gauge"):
            raise find.SearchFailed(f"bad {attr}")
        return 0.05

    monkeypatch.setattr(find, "_p_mismatch", both_fail)
    assert find.find_for_material(cid, MATERIAL["key"])["check_error"] == "bad thickness_in"


def test_a_material_with_nothing_specified_makes_no_checks(cid, mock_db, wired, monkeypatch):
    seal = {"key": "sealant", "role": "sealant", "desc": "Acoustical sealant, top and bottom of wall", "per": "lf", "source": "drawing"}
    mock_db.conditions.update_one({"_id": ObjectId(cid)}, {"$set": {"materials": [seal]}})
    calls = []
    monkeypatch.setattr(find, "_p_mismatch", lambda *a, **k: calls.append(a) or 0.5)
    doc = find.find_for_material(cid, "sealant")
    assert calls == [] and doc["checked"] is True and doc["spec_check"] == {}


@pytest.mark.parametrize("url,stored", [
    ("javascript:alert(1)", None), ("data:text/html,<b>x</b>", None), (None, None), ("//example.com/a.pdf", None),
    ("https://example.com/a.pdf", "https://example.com/a.pdf"), ("http://example.com/a.pdf", "http://example.com/a.pdf")])
def test_only_http_urls_are_stored_as_the_product_link(cid, mock_db, wired, monkeypatch, url, stored):
    monkeypatch.setattr(find, "_search", lambda q, domains=(): [{**PAGE, "url": url}])
    monkeypatch.setattr(find, "_p_mismatch", lambda material, attrs, attr, job_id: 0.05)
    doc = find.find_for_material(cid, MATERIAL["key"])
    assert doc["url"] == stored and mock_db.product_data.find_one({})["url"] == stored
