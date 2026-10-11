"""Lane 1 routes: a job's sheets, conditions and rooms, and re-parsing a sheet picked by hand."""
import pytest

from app import jev, router
from tests.extract.test_analyze import keyword_jev, parsed


@pytest.fixture
def job(client, synthetic_pdf, monkeypatch):
    """The synthetic set uploaded with a keyword-agreeing Jev and a fake parse_partition."""
    monkeypatch.setattr(jev, "choice", keyword_jev)
    monkeypatch.setattr(router, "run_task", parsed)
    with synthetic_pdf.open("rb") as f:
        r = client.post("/api/jobs", data={"name": "API"}, files=[("files", ("synthetic.pdf", f, "application/pdf"))])
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    sheets = client.get(f"/api/jobs/{job_id}/sheets").json()
    return job_id, {s["sheet_no"]: s for s in sheets}


def test_sheets_in_page_order(client, job):
    job_id, sheets = job
    listed = client.get(f"/api/jobs/{job_id}/sheets").json()
    assert [s["page"] for s in listed] == list(range(1, 8))
    assert sheets["A-501"]["kind"] == "partition_types" and sheets["A-501"]["routing"]["method"] == "jev"
    assert all(s["id"] for s in listed)


def test_conditions_from_a501(client, job):
    job_id, sheets = job
    conditions = client.get(f"/api/jobs/{job_id}/conditions").json()
    assert [c["type_tag"] for c in conditions] == ["A1", "A2", "B1"]
    assert {c["source"]["sheet"] for c in conditions} == {sheets["A-501"]["id"]}
    assert all(c["studs"]["size_in"] == 3.625 and c["studs"]["gauge"] == 20 for c in conditions)


def test_rooms_per_sheet(client, job):
    job_id, sheets = job
    rooms = client.get(f"/api/jobs/{job_id}/rooms").json()
    assert len(rooms) == 6
    a101 = client.get(f"/api/jobs/{job_id}/rooms", params={"sheet": sheets["A-101"]["id"]}).json()
    assert [(r["number"], r["name"]) for r in a101] == [("101", "OFFICE"), ("102", "CORRIDOR"), ("103", "STORAGE")]
    assert all(len(r["label_pt"]) == 2 for r in a101)


@pytest.mark.parametrize("what", ["sheets", "conditions", "rooms"])
def test_unknown_job_is_404(client, what):
    assert client.get(f"/api/jobs/000000000000000000000000/{what}").status_code == 404
    assert client.get(f"/api/jobs/not-an-id/{what}").status_code == 404


def test_reparse_updates_the_sheets_conditions_in_place(client, job):
    """D7: re-parsing upserts by (job_id, type_tag), so measurements never lose their condition."""
    job_id, sheets = job
    before = {c["id"] for c in client.get(f"/api/jobs/{job_id}/conditions").json()}
    r = client.post(f"/api/sheets/{sheets['A-501']['id']}/parse")
    assert r.status_code == 200, r.text
    assert r.json()["tags"] == ["A1", "A2", "B1"]
    after = client.get(f"/api/jobs/{job_id}/conditions").json()
    assert [c["type_tag"] for c in after] == ["A1", "A2", "B1"]
    assert before == {c["id"] for c in after}


def test_parse_a_sheet_jev_missed(client, job, monkeypatch):
    """Checkpoint A fallback: Jev filed the partition sheet as details; a person picks it and parses it."""
    job_id, sheets = job
    a501 = sheets["A-501"]
    client.post(f"/api/sheets/{a501['id']}/parse")  # start from a known state
    from app.db import get_db, oid

    db = get_db()
    db.sheets.update_one({"_id": oid(a501["id"])}, {"$set": {"kind": "details"}})
    db.conditions.delete_many({"job_id": job_id})
    r = client.post(f"/api/sheets/{a501['id']}/parse")
    assert r.status_code == 200 and r.json()["tags"] == ["A1", "A2", "B1"]
    sheet = client.get(f"/api/jobs/{job_id}/sheets").json()[a501["page"] - 1]
    assert sheet["kind"] == "partition_types"
    assert sheet["routing"]["method"] == "manual" and sheet["routing"]["previous_kind"] == "details"
    assert len(client.get(f"/api/jobs/{job_id}/conditions").json()) == 3


def test_parse_a_sheet_without_tags_changes_nothing(client, job):
    job_id, sheets = job
    r = client.post(f"/api/sheets/{sheets['A-101']['id']}/parse")
    assert r.status_code == 422
    assert client.get(f"/api/jobs/{job_id}/sheets").json()[1]["kind"] == "floor_plan"


def test_parse_unknown_sheet_is_404(client):
    assert client.post("/api/sheets/000000000000000000000000/parse").status_code == 404


def test_get_sheet_has_what_the_canvas_needs(client, job):
    job_id, sheets = job
    a101 = sheets["A-101"]
    r = client.get(f"/api/sheets/{a101['id']}")
    assert r.status_code == 200
    sheet = r.json()
    assert sheet["image_path"] and sheet["image_scale"] > 0 and sheet["size_pt"] == [2592.0, 1728.0]
    assert sheet["scale"]["label"] == "1/8\" = 1'-0\"" and sheet["scale"]["pt_per_ft"] == 9.0
    assert client.get("/api/sheets/000000000000000000000000").status_code == 404


@pytest.mark.parametrize("p1,p2,real_ft,label,pt_per_ft", [
    ([100, 100], [964, 100], 100, "calibrated 1:100", 8.64),  # 864 pt for 100 ft
    ([0, 0], [300, 400], 50, "calibrated 1:86.4", 10.0),  # a diagonal: 500 pt for 50 ft
    ([10, 10], [10, 226], 24, "calibrated 1:96", 9.0),  # 1/8" = 1'-0", measured
])
def test_calibrate_from_two_points(client, job, p1, p2, real_ft, label, pt_per_ft):
    job_id, sheets = job
    sid = sheets["A-102"]["id"]
    r = client.post(f"/api/sheets/{sid}/calibrate", json={"p1": p1, "p2": p2, "real_ft": real_ft})
    assert r.status_code == 200, r.text
    assert r.json()["label"] == label and r.json()["pt_per_ft"] == pt_per_ft
    scale = client.get(f"/api/sheets/{sid}").json()["scale"]
    assert (scale["label"], scale["pt_per_ft"], scale["source"], scale["confidence"]) == (label, pt_per_ft, "manual", 1)
    assert scale["calibration"] == {"p1": p1, "p2": p2, "real_ft": real_ft}


@pytest.mark.parametrize("body", [
    {"p1": [0, 0], "p2": [0, 0], "real_ft": 10},
    {"p1": [0, 0], "p2": [100, 0], "real_ft": 0},
    {"p1": [0, 0], "p2": [100, 0]},
    {"p1": [0], "p2": [100, 0], "real_ft": 10},
])
def test_calibrate_refuses_bad_input(client, job, body):
    job_id, sheets = job
    sid = sheets["A-102"]["id"]
    before = client.get(f"/api/sheets/{sid}").json()["scale"]
    assert client.post(f"/api/sheets/{sid}/calibrate", json=body).status_code == 422
    assert client.get(f"/api/sheets/{sid}").json()["scale"] == before


def test_calibrate_unknown_sheet_is_404(client):
    body = {"p1": [0, 0], "p2": [100, 0], "real_ft": 10}
    assert client.post("/api/sheets/000000000000000000000000/calibrate", json=body).status_code == 404
