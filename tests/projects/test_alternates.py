import pytest
from fastapi.testclient import TestClient

from app.main import app
from projects import alternates as alt


@pytest.fixture
def client(mock_db, monkeypatch):
    monkeypatch.setenv("DB_NAME", mock_db.name)
    mock_db.jobs.insert_one({"name": "WCU", "status": "ready", "created_at": alt.now()})
    return TestClient(app)


def _job(mock_db):
    return str(mock_db.jobs.find_one({"name": "WCU"})["_id"])


def test_base_bid_is_made_once_and_listed_first(client, mock_db):
    job = _job(mock_db)
    a = alt.ensure_base(job)
    b = alt.ensure_base(job)
    assert a["_id"] == b["_id"] and a["name"] == "Base bid"
    alt.create_alternate(job, "Alternate 1")
    names = [x["name"] for x in client.get(f"/api/jobs/{job}/alternates").json()]
    assert names == ["Base bid", "Alternate 1"]


def test_duplicate_or_blank_names_are_422(client, mock_db):
    job = _job(mock_db)
    assert client.post(f"/api/jobs/{job}/alternates", json={"name": "Alternate 1"}).status_code == 200
    assert client.post(f"/api/jobs/{job}/alternates", json={"name": "Alternate 1"}).status_code == 422
    assert client.post(f"/api/jobs/{job}/alternates", json={"name": "   "}).status_code == 422


def test_base_cannot_be_deleted_and_used_alternates_cannot_either(client, mock_db):
    job = _job(mock_db)
    base = str(alt.ensure_base(job)["_id"])
    a1 = alt.create_alternate(job, "Alternate 1")
    assert client.delete(f"/api/alternates/{base}").status_code == 409
    mock_db.measurements.insert_one({"job_id": job, "alternate_id": a1, "quantity": 10})
    assert client.delete(f"/api/alternates/{a1}").status_code == 409


def test_scope_treats_missing_alternate_as_base(mock_db, monkeypatch):
    monkeypatch.setenv("DB_NAME", mock_db.name)
    job = "j1"
    base = str(alt.ensure_base(job)["_id"])
    a1 = alt.create_alternate(job, "Alternate 1")
    mock_db.measurements.insert_many([{"job_id": job, "q": 1}, {"job_id": job, "alternate_id": base, "q": 2},
                                      {"job_id": job, "alternate_id": a1, "q": 4}])
    sums = lambda f: sum(m["q"] for m in mock_db.measurements.find({"job_id": job, **f}))
    assert sums(alt.scope(job, None)) == 3 and sums(alt.scope(job, base)) == 3 and sums(alt.scope(job, a1)) == 4


def test_projects_lists_jobs_with_their_alternates(client, mock_db):
    job = _job(mock_db)
    alt.create_alternate(job, "Alternate 1A")
    row = next(p for p in client.get("/api/projects").json() if p["id"] == job)
    assert [a["name"] for a in row["alternates"]] == ["Base bid", "Alternate 1A"]
