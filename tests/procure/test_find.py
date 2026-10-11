import hashlib
from datetime import datetime, timezone

import httpx
import pytest
from bson import ObjectId

from procure import find, sources

NOW = datetime.now(timezone.utc)

BOARD = {"key": "board_0.625_type_x", "role": "board", "desc": '5/8" Type X gypsum board, 2 layers each side', "thickness_in": 0.625,
         "board_type": "type_x", "per": "sf", "source": "drawing"}
STUD = {"key": "stud", "role": "stud", "desc": '3-5/8" 20 ga metal studs @ 16" o.c.', "size_in": 3.625, "gauge": 20, "spacing_in": 16,
        "per": "lf", "source": "drawing"}
TRACK = {"key": "track", "role": "track", "desc": '3-5/8" 20 ga track, top and bottom', "size_in": 3.625, "gauge": 20, "per": "lf", "source": "derived"}
SA4 = {"type_tag": "SA4", "materials": [BOARD]}
PAGE = {"url": "https://example.com/firecode-x.pdf", "title": "5/8 Type X data sheet", "markdown": "Thickness 5/8 in. Type X. UL U419."}
KEY = "board_0.625_type_x"


def qhash(query: str, domains=()) -> str:
    return hashlib.sha1((query.lower() + "|" + ",".join(sorted(domains))).encode()).hexdigest()


@pytest.fixture
def cid(mock_db, monkeypatch):
    monkeypatch.setenv("DB_NAME", mock_db.name)
    return str(mock_db.conditions.insert_one({**SA4, "job_id": "j"}).inserted_id)


def _wire(monkeypatch, calls):
    """calls collects (query, domains) for every Firecrawl search."""
    monkeypatch.setattr(find, "_search", lambda q, domains=(): calls.append((q, list(domains))) or [PAGE])
    monkeypatch.setattr(find, "_attrs", lambda text, job_id: {"thickness_in": 0.625, "board_type": "type_x"})
    monkeypatch.setattr(find, "_p_mismatch", lambda material, attrs, attr, job_id: 0.05)
    monkeypatch.setattr(find, "_key", lambda: "fc-test")


def test_find_without_key_is_unavailable(cid, mock_db, monkeypatch):
    monkeypatch.setattr(find, "_key", lambda: "")
    with pytest.raises(find.Unavailable, match="Firecrawl"):
        find.find_for_material(cid, KEY)
    assert mock_db.product_data.count_documents({}) == 0


def test_second_search_for_the_same_material_is_a_cache_hit(cid, mock_db, monkeypatch):
    calls = []
    _wire(monkeypatch, calls)
    a = find.find_for_material(cid, KEY)
    b = find.find_for_material(cid, KEY)
    assert calls == [(find.query_for(BOARD), [])] and a["id"] == b["id"]
    assert a["spec_check"] == {"thickness_in": 0.05, "board_type": 0.05} and a["checked"] is True and a["domains"] == []
    cond = mock_db.conditions.find_one({"_id": ObjectId(cid)})
    assert cond["materials"][0]["product_id"] == a["id"]


# ---- the query: built from attributes, never the stud spacing ----
def test_query_reads_like_a_supplier_search():
    assert find.query_for(BOARD) == '5/8" Type X gypsum board'
    assert find.query_for(STUD) == '3-5/8" 20 ga steel stud'
    assert find.query_for(TRACK) == '3-5/8" 20 ga steel track'


@pytest.mark.parametrize("material,query", [
    ({**BOARD, "board_type": "regular"}, '5/8" gypsum board'),
    ({**BOARD, "board_type": "moisture_resistant", "thickness_in": 0.5}, '1/2" moisture-resistant gypsum board'),
    ({**BOARD, "board_type": "cement_board"}, '5/8" cement board'),
    ({**STUD, "gauge": None}, '3-5/8" steel stud'),
    ({"key": "resilient_channel", "role": "resilient_channel", "desc": '25 ga resilient channel @ 24" o.c., both sides', "gauge": 25, "spacing_in": 24},
     "25 ga resilient channel"),
    ({"key": "bracing", "role": "bracing", "desc": 'Diagonal bracing back to structure @ 48" max', "per": "ea"}, "Diagonal bracing back to structure"),
    ({"key": "insulation", "role": "insulation", "desc": "Acoustical batt insulation, full cavity", "size_in": 3.625}, "Acoustical batt insulation"),
])
def test_query_for_other_materials(material, query):
    assert find.query_for(material) == query


def test_a_query_never_carries_the_spacing_or_runs_past_500_chars():
    for m in (STUD, {**STUD, "gauge": None}, {"key": "x", "role": "other", "desc": 'Odd thing @ 16" o.c.'}, {"key": "y", "role": "other", "desc": "w" * 900}):
        q = find.query_for(m)
        assert "@" not in q and "o.c" not in q and 0 < len(q) <= 500


# ---- the Firecrawl v2 request and response ----
class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._body


def _post(monkeypatch, resp, sent=None):
    def post(url, **kw):
        if sent is not None:
            sent.append({"url": url, **kw})
        return resp

    monkeypatch.setattr(find, "_key", lambda: "fc-test")
    monkeypatch.setattr(find.httpx, "post", post)


def test_search_posts_the_v2_body_with_the_suppliers_domains(monkeypatch):
    sent = []
    _post(monkeypatch, _Resp(200, {"success": True, "data": {"web": [PAGE, {"url": "https://b.example.com", "title": "B"}]}, "creditsUsed": 5}), sent)
    pages = find._search('5/8" Type X gypsum board', ["a-supply.com", "b-supply.com"])
    assert pages == [PAGE, {"url": "https://b.example.com", "title": "B"}]  # results come from data.web
    (call,) = sent
    assert call["url"] == "https://api.firecrawl.dev/v2/search" and call["headers"] == {"Authorization": "Bearer fc-test"}
    assert call["json"] == {"query": '5/8" Type X gypsum board', "limit": 3, "includeDomains": ["a-supply.com", "b-supply.com"],
                            "scrapeOptions": {"formats": [{"type": "markdown"}]}, "timeout": 60000}


def test_search_without_suppliers_searches_the_open_web(monkeypatch):
    sent = []
    _post(monkeypatch, _Resp(200, {"success": True, "data": {"web": [PAGE]}}), sent)
    assert find._search("q") == [PAGE] and find._search("q", []) == [PAGE]
    assert all("includeDomains" not in c["json"] for c in sent) and len(sent) == 2


def test_search_trims_the_query_to_firecrawls_500_chars(monkeypatch):
    sent = []
    _post(monkeypatch, _Resp(200, {"success": True, "data": {"web": []}}), sent)
    assert find._search("w" * 900) == [] and len(sent[0]["json"]["query"]) == 500


@pytest.mark.parametrize("body", [{"success": True, "data": {}}, {"success": True}, {"success": True, "data": {"web": None}}])
def test_search_with_no_results_is_an_empty_list(monkeypatch, body):
    _post(monkeypatch, _Resp(200, body))
    assert find._search("q") == []


@pytest.mark.parametrize("status,sentence", [
    (401, "Firecrawl rejected the key"), (402, "Firecrawl is out of credits"), (429, "Firecrawl rate limit — try again in a minute")])
def test_account_level_failures_are_blocked_with_a_plain_sentence(monkeypatch, status, sentence):
    _post(monkeypatch, _Resp(status, {"success": False, "error": "Insufficient credits to perform this request."}))
    with pytest.raises(find.SearchBlocked) as e:
        find._search("q")
    assert str(e.value) == sentence and isinstance(e.value, find.SearchFailed)


def test_a_blocked_status_with_a_non_json_body_is_still_blocked(monkeypatch):
    _post(monkeypatch, _Resp(402, None))
    with pytest.raises(find.SearchBlocked, match="out of credits"):
        find._search("q")


def test_non_json_is_a_readable_failure(monkeypatch):
    _post(monkeypatch, _Resp(200, None))
    with pytest.raises(find.SearchFailed, match="Firecrawl answered with something that wasn't JSON") as e:
        find._search("q")
    assert not isinstance(e.value, find.SearchBlocked)


def test_success_false_carries_firecrawls_reason(monkeypatch):
    _post(monkeypatch, _Resp(200, {"success": False, "error": "Invalid includeDomains"}))
    with pytest.raises(find.SearchFailed, match="Firecrawl couldn't search: Invalid includeDomains") as e:
        find._search("q")
    assert not isinstance(e.value, find.SearchBlocked)


@pytest.mark.parametrize("status,body,msg", [
    (408, {"success": False, "error": "Request timed out"}, "Firecrawl timed out"),
    (500, {"success": False, "error": "Internal error"}, "Firecrawl said 500: Internal error"),
    (502, None, "Firecrawl said 502"),
    (200, ["not", "an", "object"], "wasn't JSON")])
def test_other_failures_are_searchfailed_not_blocked(monkeypatch, status, body, msg):
    _post(monkeypatch, _Resp(status, body))
    with pytest.raises(find.SearchFailed, match=msg) as e:
        find._search("q")
    assert not isinstance(e.value, find.SearchBlocked)


def test_search_wraps_a_transport_error(monkeypatch):
    def boom(*_a, **_k):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(find, "_key", lambda: "fc-test")
    monkeypatch.setattr(find.httpx, "post", boom)
    with pytest.raises(find.SearchFailed, match="Couldn't reach Firecrawl"):
        find._search("q")


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
    monkeypatch.setattr(find, "_search", _raises(find.SearchFailed, "Firecrawl said 500"))
    with pytest.raises(find.SearchFailed, match="500"):
        find.find_for_material(cid, KEY)
    assert mock_db.product_data.count_documents({}) == 0


def test_route_without_a_key_is_409_json(client, cid, monkeypatch):
    monkeypatch.setattr(find, "_key", lambda: "")
    r = client.post(f"/api/conditions/{cid}/materials/{KEY}/products")
    assert r.status_code == 409 and "Firecrawl" in r.json()["detail"]
    assert client.post(f"/api/conditions/{cid}/procure").status_code == 409


def test_route_maps_search_failure_to_502_json(client, cid, monkeypatch):
    _wire(monkeypatch, [])
    monkeypatch.setattr(find, "_search", _raises(find.SearchBlocked, "Firecrawl is out of credits"))
    r = client.post(f"/api/conditions/{cid}/materials/{KEY}/products")
    assert r.status_code == 502 and r.json()["detail"] == "Firecrawl is out of credits"


def test_procure_all_records_a_search_failure_per_material(client, cid, monkeypatch):
    _wire(monkeypatch, [])
    monkeypatch.setattr(find, "_search", _raises(find.SearchFailed, "Firecrawl said 500"))
    r = client.post(f"/api/conditions/{cid}/procure")
    assert r.status_code == 200 and r.json() == {"products": [], "errors": {KEY: "Firecrawl said 500"}}


def test_procure_all_stops_after_one_call_when_firecrawl_blocks(client, cid, mock_db, monkeypatch):
    mock_db.conditions.update_one({"_id": ObjectId(cid)}, {"$set": {"materials": [BOARD, STUD, TRACK]}})
    _wire(monkeypatch, [])
    calls = []

    def search(q, domains=()):
        calls.append(q)
        raise find.SearchBlocked("Firecrawl is out of credits")

    monkeypatch.setattr(find, "_search", search)
    r = client.post(f"/api/conditions/{cid}/procure")
    assert r.status_code == 502 and r.json()["detail"] == "Firecrawl is out of credits"
    assert len(calls) == 1  # not one paid-for failure per material
    assert mock_db.product_data.count_documents({}) == 0


def test_procure_all_keeps_what_it_found_before_firecrawl_blocked(client, cid, mock_db, monkeypatch):
    mock_db.conditions.update_one({"_id": ObjectId(cid)}, {"$set": {"materials": [BOARD, STUD, TRACK]}})
    _wire(monkeypatch, [])
    calls = []

    def search(q, domains=()):
        calls.append(q)
        if len(calls) == 2:
            raise find.SearchBlocked("Firecrawl rate limit — try again in a minute")
        return [PAGE]

    monkeypatch.setattr(find, "_search", search)
    r = client.post(f"/api/conditions/{cid}/procure")
    assert r.status_code == 502 and "rate limit" in r.json()["detail"] and len(calls) == 2
    assert mock_db.product_data.count_documents({}) == 1  # the first material's search is saved and linked
    mats = mock_db.conditions.find_one({"_id": ObjectId(cid)})["materials"]
    assert mats[0].get("product_id") and not mats[1].get("product_id")
    assert len(client.get(f"/api/conditions/{cid}/products").json()) == 1


def test_procure_all_pulls_every_material(client, cid, mock_db, monkeypatch):
    mock_db.conditions.update_one({"_id": ObjectId(cid)}, {"$set": {"materials": [BOARD, STUD]}})
    calls = []
    _wire(monkeypatch, calls)
    r = client.post(f"/api/conditions/{cid}/procure")
    assert r.status_code == 200 and r.json()["errors"] == {} and len(r.json()["products"]) == 2
    assert [q for q, _ in calls] == ['5/8" Type X gypsum board', '3-5/8" 20 ga steel stud']


def test_a_raced_insert_falls_back_to_the_cached_doc(cid, mock_db, monkeypatch):
    """Two searches for the same query at once: query_hash is unique, so the loser re-reads the winner's doc."""
    h = qhash(find.query_for(BOARD))

    def racing_search(_query, _domains=()):
        mock_db.product_data.insert_one({"_id": ObjectId(), "query_hash": h, "url": "https://winner.example.com", "fetched_at": NOW})
        return [PAGE]

    _wire(monkeypatch, [])
    monkeypatch.setattr(find, "_search", racing_search)
    doc = find.find_for_material(cid, KEY)
    assert doc["url"] == "https://winner.example.com" and mock_db.product_data.count_documents({}) == 1


# ---- the search is saved before the Jev checks ----
def test_a_failed_jev_check_keeps_the_search_unchecked_and_still_returns_it(cid, mock_db, monkeypatch):
    calls, real_check = [], find._p_mismatch
    _wire(monkeypatch, calls)
    monkeypatch.setattr(find, "_p_mismatch", real_check)  # the real check, which wraps a JevError in SearchFailed
    monkeypatch.setattr(find.jev, "noul", _raises(find.jev.JevError, "all System One models failed"))
    doc = find.find_for_material(cid, KEY)
    assert doc["url"] == PAGE["url"] and doc["checked"] is False and doc["spec_check"] == {}
    assert "Couldn't spec-check the product" in doc["check_error"]
    saved = mock_db.product_data.find_one({})
    assert mock_db.product_data.count_documents({}) == 1 and saved["checked"] is False and saved["spec_check"] == {}
    assert saved["attrs"] == {"thickness_in": 0.625, "board_type": "type_x"} and "check_error" not in saved
    assert mock_db.conditions.find_one({"_id": ObjectId(cid)})["materials"][0]["product_id"] == doc["id"]  # the product is linked anyway
    assert len(calls) == 1


def test_an_unchecked_doc_is_checked_on_the_next_pull_without_another_search(cid, mock_db, monkeypatch):
    calls = []
    _wire(monkeypatch, calls)
    monkeypatch.setattr(find, "_p_mismatch", _raises(find.SearchFailed, "Couldn't spec-check the product: Jev is down"))
    first = find.find_for_material(cid, KEY)
    assert first["checked"] is False
    monkeypatch.setattr(find, "_p_mismatch", lambda material, attrs, attr, job_id: 0.9)  # Jev is back
    second = find.find_for_material(cid, KEY)
    assert second["id"] == first["id"] and second["checked"] is True and "check_error" not in second
    assert second["spec_check"] == {"thickness_in": 0.9, "board_type": 0.9}
    saved = mock_db.product_data.find_one({})
    assert saved["checked"] is True and saved["spec_check"] == {"thickness_in": 0.9, "board_type": 0.9}
    assert len(calls) == 1  # one paid search for the whole story


def test_a_check_still_failing_leaves_the_doc_unchecked_again(cid, mock_db, monkeypatch):
    calls = []
    _wire(monkeypatch, calls)
    monkeypatch.setattr(find, "_p_mismatch", _raises(find.SearchFailed, "Couldn't spec-check the product: down"))
    find.find_for_material(cid, KEY)
    again = find.find_for_material(cid, KEY)
    assert again["checked"] is False and "down" in again["check_error"] and len(calls) == 1


def test_a_material_with_nothing_to_check_is_checked_straight_away(cid, mock_db, monkeypatch):
    seal = {"key": "sealant_acoustical", "role": "sealant", "desc": "Acoustical sealant, top and bottom of wall", "per": "lf", "source": "drawing"}
    mock_db.conditions.update_one({"_id": ObjectId(cid)}, {"$set": {"materials": [seal]}})
    _wire(monkeypatch, [])
    monkeypatch.setattr(find, "_p_mismatch", _raises(AssertionError, "nothing to ask Jev"))
    doc = find.find_for_material(cid, "sealant_acoustical")
    assert doc["checked"] is True and doc["spec_check"] == {}


def test_an_unreadable_sheet_is_a_lookup_error(cid, mock_db, monkeypatch):
    from app.router import TaskFailed

    _wire(monkeypatch, [])
    def unreadable(*_a, **_k):
        raise TaskFailed("extract_product_attrs", "model said no")

    monkeypatch.setattr(find, "_attrs", unreadable)
    with pytest.raises(LookupError, match="Couldn't read the product sheet"):
        find.find_for_material(cid, KEY)


# ---- the supplier list is part of the cache key ----
def test_cache_hit_while_the_supplier_list_is_unchanged(cid, mock_db, monkeypatch):
    sources.add_source("b-supply.com")
    sources.add_source("https://www.a-supply.com/products")
    calls = []
    _wire(monkeypatch, calls)
    a = find.find_for_material(cid, KEY)
    b = find.find_for_material(cid, KEY)
    assert calls == [('5/8" Type X gypsum board', ["a-supply.com", "b-supply.com"])]  # sorted, www. stripped
    assert a["id"] == b["id"] and a["domains"] == ["a-supply.com", "b-supply.com"]
    assert mock_db.product_data.find_one({})["query_hash"] == qhash('5/8" Type X gypsum board', ["b-supply.com", "a-supply.com"])


def test_cache_miss_when_the_supplier_list_changes(cid, mock_db, monkeypatch):
    calls = []
    _wire(monkeypatch, calls)
    open_web = find.find_for_material(cid, KEY)
    s = sources.add_source("a-supply.com")
    with_a = find.find_for_material(cid, KEY)
    sources.add_source("b-supply.com")
    with_ab = find.find_for_material(cid, KEY)
    sources.delete_source(s["id"])
    with_b = find.find_for_material(cid, KEY)
    assert [d for _, d in calls] == [[], ["a-supply.com"], ["a-supply.com", "b-supply.com"], ["b-supply.com"]]
    assert len({open_web["id"], with_a["id"], with_ab["id"], with_b["id"]}) == 4
    sources.delete_source(sources.list_sources()[0]["id"])
    assert find.find_for_material(cid, KEY)["id"] == open_web["id"]  # back to the open web: the first doc again, no new search
    assert len(calls) == 4
    assert mock_db.conditions.find_one({"_id": ObjectId(cid)})["materials"][0]["product_id"] == open_web["id"]


def test_the_first_page_with_a_scrape_is_the_one_read(cid, mock_db, monkeypatch):
    seen = []
    _wire(monkeypatch, [])
    monkeypatch.setattr(find, "_search", lambda q, domains=(): [{"url": "https://a.example.com/empty"}, {**PAGE, "title": None}])
    monkeypatch.setattr(find, "_attrs", lambda text, job_id: seen.append(text) or {})
    doc = find.find_for_material(cid, KEY)
    assert doc["url"] == PAGE["url"] and seen == [f"\n{PAGE['markdown']}"]  # a page Firecrawl couldn't scrape is skipped; no "None" title
