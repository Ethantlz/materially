"""Fixtures for lane 1: the synthetic ARCH D set from scripts/make_drawing_set.py."""
import os
import tempfile

# app.main mounts /data from DATA_DIR when it is first imported; keep test renders out of the repo.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="materially-extract-")

import pytest  # noqa: E402

from extract.ingest import read_pages  # noqa: E402
from scripts.make_drawing_set import build  # noqa: E402


@pytest.fixture(scope="session")
def synthetic_pdf(tmp_path_factory):
    path = tmp_path_factory.mktemp("synthetic") / "synthetic.pdf"
    build(path)
    return path


@pytest.fixture
def synthetic_pages(synthetic_pdf):
    """Fresh PageText objects per test (the title-block locator mutates them)."""
    return read_pages(synthetic_pdf)


@pytest.fixture
def sheet_by_no(synthetic_pages):
    """sheet number -> PageText, using the order in scripts/make_drawing_set.SHEETS."""
    from scripts.make_drawing_set import SHEETS

    return {no: p for (no, *_), p in zip(SHEETS, synthetic_pages)}


@pytest.fixture
def client(monkeypatch):
    """The app on a fresh mongomock database, model keys blanked (fake jev/run_task where a test needs them)."""
    import uuid

    from fastapi.testclient import TestClient

    import app.main as main

    monkeypatch.setenv("MONGODB_URI", "mongomock://")
    monkeypatch.setenv("DB_NAME", f"test_{uuid.uuid4().hex[:8]}")
    monkeypatch.setenv("DATA_DIR", str(main.data_dir))  # where /data is mounted
    for key in ("OPENROUTER_API_KEY", "LITELLM_API_KEY", "LUMINAL_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(key, "")
    with TestClient(main.app) as c:
        yield c
