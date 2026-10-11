"""Upload a synthetic drawing set end to end: names, kinds and auto scales come back from the API.

Runs offline on mongomock with the model keys blanked, so kinds are the title-keyword kinds. The env is set
per test (monkeypatch), not at import, so live_db tests collected in the same run still see the real URI.
"""
import os
import tempfile
import uuid

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="materially-")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as main  # noqa: E402
from extract.scale import parse_label  # noqa: E402
from scripts.make_drawing_set import build  # noqa: E402

app = main.app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MONGODB_URI", "mongomock://")
    monkeypatch.setenv("DB_NAME", f"test_{uuid.uuid4().hex[:8]}")
    monkeypatch.setenv("DATA_DIR", str(main.data_dir))  # where /data is mounted, whoever imported app.main first
    for key in ("OPENROUTER_API_KEY", "LITELLM_API_KEY", "LUMINAL_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(key, "")
    with TestClient(app) as c:
        yield c


def test_upload_names_and_scales(client, tmp_path):
    pdf = tmp_path / "set.pdf"
    expected = build(pdf)
    with pdf.open("rb") as f:
        r = client.post("/api/jobs", data={"name": "Test"}, files=[("files", ("set.pdf", f, "application/pdf"))])
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    body = client.get(f"/api/jobs/{job_id}").json()
    assert body["job"]["status"] == "ready"
    sheets = body["sheets"]
    assert [(s["sheet_no"], s["title"], s["kind"], s["scale"]["label"], s["scale"]["source"]) for s in sheets] == [
        (e["sheet_no"], e["title"], e["kind"], e["scale"], e["scale_source"]) for e in expected
    ]
    assert all(s["thumb_path"] and client.get(s["thumb_path"]).status_code == 200 for s in sheets)
    assert client.get(f"/jobs/{job_id}").status_code == 200
    assert client.get("/").status_code == 200

    r = client.patch(f"/api/sheets/{sheets[1]['id']}/scale", json={"label": "1/4"})
    assert r.json()["label"] == "1/4\" = 1'-0\"" and r.json()["source"] == "manual"


def test_parse_labels():
    assert parse_label("1/8\" = 1'-0\"").pt_per_ft == 9.0
    assert parse_label("1 1/2\" = 1'-0\"").pt_per_ft == 108.0
    assert parse_label("1\" = 20'").pt_per_ft == 3.6
    assert parse_label("1:100").ratio == 100
    assert parse_label("NTS").pt_per_ft is None
    assert parse_label("3/16″ = 1′-0″").label == "3/16\" = 1'-0\""
