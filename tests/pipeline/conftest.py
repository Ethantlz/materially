"""Fixtures for the pipeline lane.

db        runs the test twice where it can: on mongomock (python fallbacks for $near, $merge, change streams)
          and on the lane's real database (the real $near, $merge and change stream). The live run is skipped
          outside `infisical run`.
job       a fresh job_id per test; whatever the test wrote for it is deleted afterwards (live_job: live db only).
client    FastAPI TestClient over the pipeline router alone.
"""
import uuid

import pytest
from bson import ObjectId
from fastapi import FastAPI
from fastapi.testclient import TestClient

from contracts.samples import SAMPLES
from pipeline.api import router

JOB_COLLECTIONS = ("rooms", "ewos", "ewo_drafts", "conditions", "orders", "usage")


@pytest.fixture
def mdb(mock_db, monkeypatch):
    """mock_db, made the database get_db() returns (get_db() reads DB_NAME, which mock_db doesn't set)."""
    monkeypatch.setenv("DB_NAME", mock_db.name)
    return mock_db


@pytest.fixture(params=["mock", "live"])
def db(request):
    return request.getfixturevalue("mdb" if request.param == "mock" else "live_db")


def fresh_job(db):
    """Yield a new job_id, then delete whatever the test wrote for it."""
    job_id = uuid.uuid4().hex[:24]
    yield job_id
    for name in JOB_COLLECTIONS:
        db[name].delete_many({"job_id": job_id})
    db.review_queue.delete_many({"doc.job_id": job_id})


@pytest.fixture
def job(db):
    yield from fresh_job(db)


@pytest.fixture
def live_job(live_db):
    yield from fresh_job(live_db)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        yield c


def new_id() -> str:
    return str(ObjectId())


def by_id(collection, hex_id: str) -> dict | None:
    return collection.find_one({"_id": ObjectId(hex_id)})


def add_condition(db, job_id: str, **over) -> str:
    """The sample A1 condition (3-5/8 20 ga at 16, one layer 5/8 Type X each side) on this job."""
    doc = {k: v for k, v in SAMPLES["conditions"].items() if k != "_id"}
    return str(db.conditions.insert_one({**doc, "job_id": job_id, **over}).inserted_id)
