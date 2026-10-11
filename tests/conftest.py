"""Shared fixtures for every lane.

mock_db: an in-memory MongoDB (mongomock) with the contracts' collections, indexes and seeds
         (model_policy, price_book). No document validation and no change streams.
live_db: the lane's real database (DB_NAME, e.g. lane2), for validators, $near and change streams.
         Skipped unless MONGODB_URI points at a real server:
         infisical run --env=dev --path=/ -- uv run pytest tests/pipeline -q
"""
import os
import uuid

import pytest

from app.db import get_db
from contracts.setup_db import setup


@pytest.fixture
def mock_db(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "mongomock://")
    db = get_db(f"test_{uuid.uuid4().hex[:8]}")
    setup(db)
    yield db
    db.client.drop_database(db.name)


@pytest.fixture
def live_db():
    uri = os.environ.get("MONGODB_URI", "")
    if not uri or uri.startswith("mongomock://"):
        pytest.skip("needs a real MONGODB_URI (run under `infisical run`)")
    return get_db()
