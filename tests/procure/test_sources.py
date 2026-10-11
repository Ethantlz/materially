"""The company-wide supplier list product searches are limited to."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from contracts.validate import validate
from procure import sources


@pytest.fixture
def db(mock_db, monkeypatch):
    monkeypatch.setenv("DB_NAME", mock_db.name)
    return mock_db


@pytest.fixture
def client(db):
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("typed,url,domain", [
    ("gypsum-supply.com", "https://gypsum-supply.com", "gypsum-supply.com"),
    ("  WWW.Gypsum-Supply.COM/shop/board?x=1#top  ", "https://www.gypsum-supply.com/shop/board?x=1", "gypsum-supply.com"),
    ("http://www.certainteed.com/", "http://www.certainteed.com/", "certainteed.com"),
    ("https://user:pw@Pro.Example.com:8443/a", "https://pro.example.com:8443/a", "pro.example.com"),
])
def test_a_typed_address_is_normalised(db, typed, url, domain):
    s = sources.add_source(typed)
    assert s["url"] == url and s["domain"] == domain and s["name"] is None
    assert validate("product_sources", db.product_sources.find_one({"domain": domain})) == []


def test_name_is_kept_trimmed_and_blank_means_none(db):
    assert sources.add_source("a-supply.com", "  Acme Supply ")["name"] == "Acme Supply"
    assert sources.add_source("b-supply.com", "   ")["name"] is None


@pytest.mark.parametrize("bad", ["", "   ", "https://", "not a url", "javascript:alert(1)", "ftp://files.example.com", "localhost", "http://exa mple.com"])
def test_an_address_without_a_hostname_is_rejected(db, bad):
    with pytest.raises(ValueError):
        sources.add_source(bad)
    assert db.product_sources.count_documents({}) == 0


def test_the_same_domain_is_stored_once(db):
    a = sources.add_source("https://www.Gypsum-Supply.com/boards", "Gypsum Supply")
    b = sources.add_source("gypsum-supply.com", "Another name")
    assert a["id"] == b["id"] and b["name"] == "Gypsum Supply"  # the existing doc comes back, untouched
    assert db.product_sources.count_documents({}) == 1


def test_list_and_delete(db):
    a = sources.add_source("a-supply.com")
    b = sources.add_source("b-supply.com", "B")
    assert [s["domain"] for s in sources.list_sources()] == ["a-supply.com", "b-supply.com"]
    sources.delete_source(a["id"])
    assert [s["id"] for s in sources.list_sources()] == [b["id"]]
    with pytest.raises(LookupError):
        sources.delete_source(a["id"])
    with pytest.raises(LookupError):
        sources.delete_source("not-an-id")


def test_routes_add_list_and_remove(client, db):
    r = client.post("/api/product-sources", json={"url": "www.gypsum-supply.com", "name": "Gypsum Supply"})
    assert r.status_code == 200 and r.json()["domain"] == "gypsum-supply.com"
    sid = r.json()["id"]
    again = client.post("/api/product-sources", json={"url": "https://gypsum-supply.com/x"})
    assert again.status_code == 200 and again.json()["id"] == sid
    rows = client.get("/api/product-sources").json()
    assert [s["id"] for s in rows] == [sid] and rows[0]["name"] == "Gypsum Supply"
    gone_ok = client.delete(f"/api/product-sources/{sid}")
    assert gone_ok.status_code == 200 and gone_ok.json() == {"deleted": sid}
    assert client.get("/api/product-sources").json() == []
    gone = client.delete(f"/api/product-sources/{sid}")
    assert gone.status_code == 404 and "No such" in gone.json()["detail"]


def test_route_rejects_an_address_with_no_hostname_as_422(client, db):
    r = client.post("/api/product-sources", json={"url": "https://"})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str) and "website address" in r.json()["detail"]
    empty = client.post("/api/product-sources", json={})
    assert empty.status_code == 422 and isinstance(empty.json()["detail"], str)  # a sentence too, not FastAPI's list
    assert db.product_sources.count_documents({}) == 0
