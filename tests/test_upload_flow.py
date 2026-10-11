"""Upload a synthetic drawing set end to end: names, kinds and auto scales come back from the API."""
import os
import tempfile

os.environ["MONGODB_URI"] = "mongomock://"
os.environ["DB_NAME"] = "test"
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="materially-")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from extract.scale import parse_label  # noqa: E402
from scripts.make_drawing_set import build  # noqa: E402


def test_upload_names_and_scales(tmp_path):
    pdf = tmp_path / "set.pdf"
    expected = build(pdf)
    with TestClient(app) as client:
        with pdf.open("rb") as f:
            r = client.post("/api/jobs", data={"name": "Test"}, files=[("files", ("set.pdf", f, "application/pdf"))])
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        body = client.get(f"/api/jobs/{job_id}").json()
        assert body["job"]["status"] == "ready"
        sheets = body["sheets"]
        assert [(s["sheet_no"], s["title"], s["kind"], s["scale"]["label"]) for s in sheets] == [
            (e["sheet_no"], e["title"], e["kind"], e["scale"]) for e in expected
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
