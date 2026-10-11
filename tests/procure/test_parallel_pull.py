"""POST /api/conditions/{cid}/procure pulls a condition's materials PULL_AT_ONCE at a time.
No network: find._search / _attrs / _p_mismatch / _key are faked. Timing assertions are generous (a loaded machine
must not flake them), and each one is backed by a count of how many searches were in flight at once."""
import logging
import threading
import time

import pytest
from bson import ObjectId

from procure import api, find

PAGE = {"url": "https://example.com/sheet.pdf", "title": "Data sheet", "markdown": "Thickness 5/8 in."}
BLOCKED = "Firecrawl is out of credits"
NO_KEY = "Product search needs a Firecrawl key (FIRECRAWL_API_KEY in Infisical)."


def mat(i, **extra):
    """A material with nothing for Jev to check, and a query of its own ('Product 3')."""
    return {"key": f"m{i}", "role": "other", "desc": f"Product {i}", "per": "ea", **extra}


def query(i):
    return f"Product {i}"


class Probe:
    """Stands in for find._search: records every call and how many were in flight at the same moment."""

    def __init__(self, delay=0.0, behave=None):
        self.delay, self.behave = delay, behave or {}
        self.calls, self.in_flight, self.peak = [], 0, 0
        self.lock = threading.Lock()

    def __call__(self, q, domains=()):
        with self.lock:
            self.calls.append(q)
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
        try:
            time.sleep(self.behave.get(q, {}).get("delay", self.delay))
            act = self.behave.get(q, {}).get("then")
            if act:
                act()
            return [PAGE]
        finally:
            with self.lock:
                self.in_flight -= 1


def raising(exc):
    def f():
        raise exc
    return f


@pytest.fixture
def cid(mock_db, monkeypatch):
    monkeypatch.setenv("DB_NAME", mock_db.name)
    return str(mock_db.conditions.insert_one({"type_tag": "SA4", "materials": [], "job_id": "j"}).inserted_id)


@pytest.fixture
def client(cid):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setattr(find, "_key", lambda: "fc-test")
    monkeypatch.setattr(find, "_attrs", lambda text, job_id: {})
    monkeypatch.setattr(find, "_p_mismatch", lambda material, attrs, attr, job_id: 0.05)


@pytest.fixture
def pull(cid, mock_db, client, fakes, monkeypatch):
    """pull(materials, probe) sets the condition's materials, wires the search and POSTs /procure."""

    def go(materials, probe):
        mock_db.conditions.update_one({"_id": ObjectId(cid)}, {"$set": {"materials": materials}})
        monkeypatch.setattr(find, "_search", probe)
        return client.post(f"/api/conditions/{cid}/procure")

    return go


def test_the_default_is_four_materials_at_once():
    assert api.PULL_AT_ONCE == 4


def test_five_materials_are_pulled_at_once_and_come_back_in_material_order(pull, cid, mock_db):
    mats, probe = [mat(i) for i in range(5)], Probe(delay=0.3)
    t0 = time.perf_counter()
    r = pull(mats, probe)
    took = time.perf_counter() - t0
    assert r.status_code == 200 and r.json()["errors"] == {}
    assert [p["component"] for p in r.json()["products"]] == [m["key"] for m in mats]  # material order, not finishing order
    assert took < 1.2  # one after another would be >= 1.5 s
    assert 2 <= probe.peak <= api.PULL_AT_ONCE and len(probe.calls) == 5
    linked = [m["product_id"] for m in mock_db.conditions.find_one({"_id": ObjectId(cid)})["materials"]]
    assert linked == [p["id"] for p in r.json()["products"]]  # each material's own product_id, none lost to a neighbour


def test_a_dozen_materials_are_each_linked_to_their_own_product(pull, cid, mock_db):
    mats = [mat(i) for i in range(12)]
    r = pull(mats, Probe(delay=0.02))
    assert r.status_code == 200 and r.json()["errors"] == {}
    assert [p["component"] for p in r.json()["products"]] == [m["key"] for m in mats]
    stored = mock_db.conditions.find_one({"_id": ObjectId(cid)})["materials"]
    assert [m["key"] for m in stored] == [m["key"] for m in mats]  # the positional updates left the rest of the array alone
    assert [m["product_id"] for m in stored] == [p["id"] for p in r.json()["products"]]
    assert mock_db.product_data.count_documents({}) == 12


def test_pull_at_once_caps_the_searches_in_flight(pull, monkeypatch):
    monkeypatch.setattr(api, "PULL_AT_ONCE", 2)
    probe = Probe(delay=0.05)
    assert pull([mat(i) for i in range(6)], probe).status_code == 200
    assert probe.peak == 2 and len(probe.calls) == 6


# ---- Firecrawl refuses the account: stop starting new searches ----
def test_a_block_stops_new_searches_and_answers_502(pull, mock_db):
    probe = Probe(delay=0.05, behave={query(i): {"then": raising(find.SearchBlocked(BLOCKED))} for i in range(12)})
    r = pull([mat(i) for i in range(12)], probe)
    assert r.status_code == 502 and r.json()["detail"] == BLOCKED
    assert 1 <= len(probe.calls) <= api.PULL_AT_ONCE  # at most the searches already running, not one per material
    assert mock_db.product_data.count_documents({}) == 0


def test_a_block_lets_the_running_searches_finish_and_keeps_what_they_found(pull, mock_db, monkeypatch):
    monkeypatch.setattr(api, "PULL_AT_ONCE", 2)
    probe = Probe(behave={query(0): {"delay": 0.6}, query(1): {"then": raising(find.SearchBlocked(BLOCKED))}})
    r = pull([mat(i) for i in range(6)], probe)
    assert r.status_code == 502 and r.json()["detail"] == BLOCKED
    assert sorted(probe.calls) == [query(0), query(1)]  # m0 was already running; m2..m5 never started
    assert [d["component"] for d in mock_db.product_data.find({})] == ["m0"]  # and m0's paid search is saved and linked
    stored = mock_db.conditions.find_one({})["materials"]
    assert stored[0].get("product_id") and not any(m.get("product_id") for m in stored[1:])


# ---- one material's trouble stays its own ----
def test_an_unexpected_error_in_one_material_leaves_the_others(pull, caplog):
    probe = Probe(behave={query(2): {"then": raising(RuntimeError("boom"))}})
    with caplog.at_level(logging.ERROR, logger="materially.procure"):
        r = pull([mat(i) for i in range(4)], probe)
    assert r.status_code == 200
    assert [p["component"] for p in r.json()["products"]] == ["m0", "m1", "m3"]
    assert r.json()["errors"] == {"m2": "Couldn't search for this material."}
    assert any("m2" in rec.getMessage() and rec.exc_info for rec in caplog.records)  # logged with its traceback, not shown to the user


def test_searchfailed_is_still_recorded_per_material(pull):
    probe = Probe(behave={query(1): {"then": raising(find.SearchFailed("Firecrawl said 500"))}})
    r = pull([mat(i) for i in range(3)], probe)
    assert r.status_code == 200 and r.json()["errors"] == {"m1": "Firecrawl said 500"}
    assert [p["component"] for p in r.json()["products"]] == ["m0", "m2"]


def test_a_search_with_no_results_is_that_materials_error_only(pull):
    def search(q, domains=()):
        return [] if q == query(1) else [PAGE]

    r = pull([mat(i) for i in range(3)], search)
    assert r.status_code == 200 and r.json()["errors"] == {"m1": f"No product data found for {query(1)!r}."}
    assert [p["component"] for p in r.json()["products"]] == ["m0", "m2"]


# ---- the key ----
def test_no_key_is_409_before_any_work(client, cid, mock_db, monkeypatch):
    mock_db.conditions.update_one({"_id": ObjectId(cid)}, {"$set": {"materials": [mat(i) for i in range(3)]}})
    probe, pulled = Probe(), []
    monkeypatch.setattr(find, "_key", lambda: "")
    monkeypatch.setattr(find, "_search", probe)
    monkeypatch.setattr(find, "find_for_material", lambda *a: pulled.append(a))
    r = client.post(f"/api/conditions/{cid}/procure")
    assert r.status_code == 409 and r.json()["detail"] == NO_KEY
    assert probe.calls == [] and pulled == [] and mock_db.product_data.count_documents({}) == 0


def test_a_key_that_goes_missing_mid_pull_is_still_409(pull, monkeypatch):
    keys = iter(["fc-test"])  # present for the up-front check, gone for every search after it
    monkeypatch.setattr(find, "_key", lambda: next(keys, ""))
    probe = Probe()
    r = pull([mat(i) for i in range(3)], probe)
    assert r.status_code == 409 and "Firecrawl" in r.json()["detail"] and probe.calls == []


def test_a_missing_condition_is_404_whatever_the_key(client, monkeypatch):
    monkeypatch.setattr(find, "_key", lambda: "")
    assert client.post(f"/api/conditions/{ObjectId()}/procure").status_code == 404
    assert client.post("/api/conditions/not-an-id/procure").status_code == 404


def test_a_condition_with_no_materials_pulls_nothing(pull):
    r = pull([], Probe())
    assert r.status_code == 200 and r.json() == {"products": [], "errors": {}}


# ---- two materials, one query ----
def test_two_materials_with_the_same_query_end_up_on_one_product(pull, cid, mock_db):
    """Both miss the cache (the barrier holds each search until the other has started), both try to insert the same
    query_hash: one wins, the other falls back to the winner's doc. Neither is an error."""
    barrier = threading.Barrier(2, timeout=10)

    def search(q, domains=()):
        barrier.wait()
        return [PAGE]

    twins = [{**mat(0), "key": "a", "desc": "Same product"}, {**mat(0), "key": "b", "desc": "Same product"}]
    r = pull(twins, search)
    assert r.status_code == 200 and r.json()["errors"] == {}
    first, second = r.json()["products"]
    assert first["id"] == second["id"] and mock_db.product_data.count_documents({}) == 1
    assert mock_db.product_data.find_one({})["checked"] is True
    assert [m["product_id"] for m in mock_db.conditions.find_one({"_id": ObjectId(cid)})["materials"]] == [first["id"]] * 2


# ---- one pull per condition at a time ----
ALREADY = "Already pulling product data for this condition."


def test_a_condition_cannot_be_pulled_twice_at_once(cid, client, mock_db, fakes, monkeypatch):
    mock_db.conditions.update_one({"_id": ObjectId(cid)}, {"$set": {"materials": [mat(0), mat(1)]}})
    other = str(mock_db.conditions.insert_one({"type_tag": "SA5", "materials": [mat(5)], "job_id": "j"}).inserted_id)
    entered, release, calls = threading.Event(), threading.Event(), []

    def search(q, domains=()):
        calls.append(q)
        if q != query(5):  # the first condition's searches hang until the test lets go
            entered.set()
            release.wait(30)
        return [PAGE]

    monkeypatch.setattr(find, "_search", search)
    first = {}
    t = threading.Thread(target=lambda: first.update(r=client.post(f"/api/conditions/{cid}/procure")))
    t.start()
    try:
        assert entered.wait(10)
        second = client.post(f"/api/conditions/{cid}/procure")
        assert second.status_code == 409 and second.json()["detail"] == ALREADY
        assert len(calls) == len(set(calls))  # the refused click searched nothing: no query was run twice
        assert client.post(f"/api/conditions/{other}/procure").status_code == 200  # another condition isn't held up
    finally:
        release.set()
        t.join(30)
    assert first["r"].status_code == 200 and len(first["r"].json()["products"]) == 2
    assert sorted(calls) == [query(0), query(1), query(5)]
    again = client.post(f"/api/conditions/{cid}/procure")  # the lock was let go: a later pull works (and is all cache hits)
    assert again.status_code == 200 and len(again.json()["products"]) == 2 and len(calls) == 3


def test_the_condition_is_released_after_a_failed_pull(pull):
    blocked = Probe(behave={query(i): {"then": raising(find.SearchBlocked(BLOCKED))} for i in range(2)})
    assert pull([mat(0), mat(1)], blocked).status_code == 502
    assert pull([mat(0), mat(1)], Probe()).status_code == 200  # not "already pulling"
