import hashlib
from datetime import datetime, timezone

import httpx
import pytest
from bson import ObjectId

from procure import find

NOW = datetime.now(timezone.utc)

SA4 = {"type_tag": "SA4", "materials": [
    {"key": "board_0.625_type_x", "role": "board", "desc": '5/8" Type X gypsum board', "thickness_in": 0.625, "board_type": "type_x", "per": "sf", "source": "drawing"}]}
PAGE = {"url": "https://example.com/firecode-x.pdf", "title": "5/8 Type X data sheet", "markdown": "Thickness 5/8 in. Type X. UL U419."}


@pytest.fixture
def cid(mock_db, monkeypatch):
    monkeypatch.setenv("DB_NAME", mock_db.name)
    return str(mock_db.conditions.insert_one({**SA4, "job_id": "j"}).inserted_id)


def _wire(monkeypatch, calls):
    monkeypatch.setattr(find, "_search", lambda q: calls.append(q) or [PAGE])
    monkeypatch.setattr(find, "_attrs", lambda text, job_id: {"thickness_in": 0.625, "board_type": "type_x"})
    monkeypatch.setattr(find, "_p_mismatch", lambda material, attrs, attr, job_id: 0.05)
    monkeypatch.setattr(find, "_key", lambda: "fc-test")


def test_find_without_key_is_unavailable(cid, mock_db, monkeypatch):
    monkeypatch.setattr(find, "_key", lambda: "")
    with pytest.raises(find.Unavailable, match="Firecrawl"):
        find.find_for_material(cid, "board_0.625_type_x")
    assert mock_db.product_data.count_documents({}) == 0


def test_second_search_for_the_same_material_is_a_cache_hit(cid, mock_db, monkeypatch):
    calls = []
    _wire(monkeypatch, calls)
    a = find.find_for_material(cid, "board_0.625_type_x")
    b = find.find_for_material(cid, "board_0.625_type_x")
    assert calls == [find.query_for(SA4["materials"][0])] and a["id"] == b["id"]
    assert a["spec_check"] == {"thickness_in": 0.05, "board_type": 0.05}
    cond = mock_db.conditions.find_one({"_id": ObjectId(cid)})
    assert cond["materials"][0]["product_id"] == a["id"]


def test_query_reads_like_a_supplier_search():
    assert find.query_for(SA4["materials"][0]) == '5/8" Type X gypsum board product data sheet'


# ---- failures the UI has to show: SearchFailed (502), never a bare plain-text 500 ----
def _raises(exc, msg):
    def f(*_a, **_k):
        raise exc(msg)
    return f


@pytest.fixture
def client(cid, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


def test_a_search_failure_is_searchfailed_and_writes_nothing(cid, mock_db, monkeypatch):
    _wire(monkeypatch, [])
    monkeypatch.setattr(find, "_search", _raises(find.SearchFailed, "Firecrawl said 402"))
    with pytest.raises(find.SearchFailed, match="402"):
        find.find_for_material(cid, "board_0.625_type_x")
    assert mock_db.product_data.count_documents({}) == 0


def test_search_wraps_a_transport_error(monkeypatch):
    monkeypatch.setattr(find, "_key", lambda: "fc-test")
    monkeypatch.setattr(find.httpx, "post", _raises(httpx.ConnectError, "no route to host"))
    with pytest.raises(find.SearchFailed, match="Couldn't reach Firecrawl"):
        find._search("q")


def test_search_wraps_a_http_status(monkeypatch):
    class _Resp:
        status_code = 429

        def raise_for_status(self):
            raise httpx.HTTPStatusError("too many", request=httpx.Request("POST", find.FIRECRAWL), response=self)

    monkeypatch.setattr(find, "_key", lambda: "fc-test")
    monkeypatch.setattr(find.httpx, "post", lambda *a, **k: _Resp())
    with pytest.raises(find.SearchFailed, match="Firecrawl said 429"):
        find._search("q")


def test_a_jev_failure_is_searchfailed(cid, mock_db, monkeypatch):
    monkeypatch.setattr(find, "_search", lambda q: [PAGE])
    monkeypatch.setattr(find, "_attrs", lambda text, job_id: {"thickness_in": 0.625, "board_type": "type_x"})
    monkeypatch.setattr(find, "_key", lambda: "fc-test")
    monkeypatch.setattr(find.jev, "noul", _raises(find.jev.JevError, "all System One models failed"))
    with pytest.raises(find.SearchFailed, match="Couldn't spec-check the product"):
        find.find_for_material(cid, "board_0.625_type_x")
    assert mock_db.product_data.count_documents({}) == 0


def test_route_without_a_key_is_409_json(client, cid, monkeypatch):
    monkeypatch.setattr(find, "_key", lambda: "")
    r = client.post(f"/api/conditions/{cid}/materials/board_0.625_type_x/products")
    assert r.status_code == 409 and "Firecrawl" in r.json()["detail"]


def test_route_maps_search_failure_to_502_json(client, cid, monkeypatch):
    _wire(monkeypatch, [])
    monkeypatch.setattr(find, "_search", _raises(find.SearchFailed, "Firecrawl said 402"))
    r = client.post(f"/api/conditions/{cid}/materials/board_0.625_type_x/products")
    assert r.status_code == 502 and r.json()["detail"] == "Firecrawl said 402"


def test_procure_all_records_a_search_failure_per_material(client, cid, monkeypatch):
    _wire(monkeypatch, [])
    monkeypatch.setattr(find, "_search", _raises(find.SearchFailed, "Firecrawl said 402"))
    r = client.post(f"/api/conditions/{cid}/procure")
    assert r.status_code == 200 and r.json() == {"products": [], "errors": {"board_0.625_type_x": "Firecrawl said 402"}}


def test_a_raced_insert_falls_back_to_the_cached_doc(cid, mock_db, monkeypatch):
    """Two searches for the same query at once: query_hash is unique, so the loser re-reads the winner's doc."""
    qhash = hashlib.sha1(find.query_for(SA4["materials"][0]).lower().encode()).hexdigest()

    def racing_search(_query):
        mock_db.product_data.insert_one({"_id": ObjectId(), "query_hash": qhash, "url": "https://winner.example.com",
                                         "fetched_at": NOW})
        return [PAGE]

    _wire(monkeypatch, [])
    monkeypatch.setattr(find, "_search", racing_search)
    doc = find.find_for_material(cid, "board_0.625_type_x")
    assert doc["url"] == "https://winner.example.com" and mock_db.product_data.count_documents({}) == 1
