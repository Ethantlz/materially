import pytest
from bson import ObjectId

from procure import find

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
