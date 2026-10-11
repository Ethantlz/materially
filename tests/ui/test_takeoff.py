"""The takeoff workspace: its server-side bootstrap, the stand-in routes behind it, and the JS it ships."""
import json
import re
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

from app.main import app
from contracts.samples import AT, CONDITION_ID, JOB_ID, SAMPLES, SHEET_PT_ID
from ui.common import condition_name, condition_views, discipline, next_color, stud_text
from ui.standins import quantity

ROOT = Path(__file__).resolve().parents[2]
PLAN_ID = ObjectId()
OTHER_ID = ObjectId()
PT_PER_FT = 9.0  # 1/8" = 1'-0"


@pytest.fixture
def client(mock_db, monkeypatch):
    monkeypatch.setenv("DB_NAME", mock_db.name)
    mock_db.jobs.insert_one(deepcopy(SAMPLES["jobs"]))
    mock_db.sheets.insert_one(deepcopy(SAMPLES["sheets"]))
    mock_db.sheets.insert_one({
        "_id": PLAN_ID, "job_id": str(JOB_ID), "page": 5, "file": "s.pdf", "file_page": 5, "sheet_no": "A-101", "title": "FIRST FLOOR PLAN",
        "kind": "floor_plan", "size_pt": [2592.0, 1728.0], "image_path": "/data/x/pages/0005.png", "thumb_path": "/data/x/pages/0005.thumb.png",
        "image_scale": 1.5, "scale": {"label": '1/8" = 1\'-0"', "pt_per_ft": PT_PER_FT, "source": "title_block"}, "created_at": AT,
    })
    mock_db.sheets.insert_one({"_id": OTHER_ID, "job_id": str(JOB_ID), "page": 6, "file": "s.pdf", "file_page": 6, "sheet_no": "S-201",
                               "title": "FRAMING", "kind": "details", "size_pt": [2592.0, 1728.0], "scale": None, "created_at": AT})
    mock_db.conditions.insert_one(deepcopy(SAMPLES["conditions"]))
    mock_db.conditions.insert_one({
        "_id": ObjectId(), "job_id": str(JOB_ID), "type_tag": "SA4", "studs": {"size_in": 3.625, "gauge": None, "spacing_in": None},
        "layers_each_side": [{"thickness_in": 0.625, "board_type": "type_x"}], "sides": 2,
    })
    return TestClient(app)


def _measure(client, kind="linear", points=None, cond=CONDITION_ID, sheet=PLAN_ID):
    points = points or [[400.0, 500.0], [1264.0, 500.0]]
    return client.post("/ui/standin/measurements", json={
        "job_id": str(JOB_ID), "sheet": str(sheet), "condition_id": str(cond), "kind": kind, "points": points, "height_ft": 10})


# ---- geometry: the same math as pipeline/takeoff.py ----
def test_quantity_math():
    assert quantity("linear", [[400, 500], [1264, 500]], PT_PER_FT) == pytest.approx(96.0)  # 864 pt at 9 pt/ft
    assert quantity("linear", [[0, 0], [90, 0], [90, 120]], PT_PER_FT) == pytest.approx(10 + 13.3333, abs=1e-3)
    assert quantity("area", [[0, 0], [90, 0], [90, 90], [0, 90]], PT_PER_FT) == pytest.approx(100.0)  # 10 ft x 10 ft
    assert quantity("area", [[0, 0], [2, 0], [2, 2], [0, 2]], PT_PER_FT) == pytest.approx(4 / 81)
    assert quantity("count", [[1, 1], [2, 2], [3, 3]], None) == 3
    with pytest.raises(ValueError, match="scale"):
        quantity("linear", [[0, 0], [1, 1]], None)


# ---- stand-in routes ----
def test_standin_measurement_roundtrip(client, mock_db):
    r = _measure(client)
    assert r.status_code == 200
    m = r.json()["measurement"]
    assert m["quantity"] == 96.0 and m["unit"] == "lf" and m["pt_per_ft"] == PT_PER_FT
    assert mock_db.measurements.count_documents({"sheet": str(PLAN_ID)}) == 1
    listed = client.get(f"/ui/standin/sheets/{PLAN_ID}/measurements").json()
    assert [x["id"] for x in listed] == [r.json()["id"]]


def test_standin_needs_a_scale_and_enough_points(client):
    r = _measure(client, sheet=OTHER_ID)
    assert r.status_code == 422 and "scale" in r.json()["detail"].lower()
    assert _measure(client, "area", [[0, 0], [10, 10]]).status_code == 422
    assert _measure(client, "count", [[10, 10]], sheet=OTHER_ID).status_code == 200  # a count needs no scale
    assert _measure(client, cond=ObjectId()).status_code == 404


def test_standin_quantities_group_by_condition_and_unit(client):
    sa4 = next(c for c in client.get("/ui/jobs/%s/data" % JOB_ID).json()["conditions"] if c["type_tag"] == "SA4")["id"]
    _measure(client)
    _measure(client, points=[[0, 0], [90, 0]])
    _measure(client, "area", [[0, 0], [90, 0], [90, 90], [0, 90]])
    _measure(client, "count", [[1, 1], [2, 2]], cond=sa4)
    rows = client.get(f"/ui/standin/jobs/{JOB_ID}/quantities").json()
    by = {(r["type_tag"], r["unit"]): r for r in rows}
    assert by[("A1", "lf")]["quantity"] == pytest.approx(106.0) and by[("A1", "lf")]["count"] == 2
    assert by[("A1", "sf")]["quantity"] == pytest.approx(100.0)
    assert by[("SA4", "ea")]["quantity"] == 2
    assert [r["type_tag"] for r in rows] == sorted(r["type_tag"] for r in rows)
    assert by[("A1", "lf")]["name"].startswith('A1 - 3-5/8"') and by[("A1", "lf")]["color"]


def test_standin_undo_and_delete(client, mock_db):
    first, second = _measure(client).json()["id"], _measure(client, points=[[0, 0], [90, 0]]).json()["id"]
    assert client.post(f"/ui/standin/sheets/{PLAN_ID}/measurements/undo").json()["deleted"] == second
    assert client.delete(f"/ui/standin/measurements/{first}").status_code == 200
    assert client.delete(f"/ui/standin/measurements/{first}").status_code == 404
    assert client.post(f"/ui/standin/sheets/{PLAN_ID}/measurements/undo").status_code == 404
    assert mock_db.measurements.count_documents({}) == 0


def test_standin_calibrate_sets_the_scale(client, mock_db):
    r = client.post(f"/ui/standin/sheets/{OTHER_ID}/calibrate", json={"p1": [100, 100], "p2": [100, 460], "real_ft": 40})
    assert r.status_code == 200
    scale = mock_db.sheets.find_one({"_id": OTHER_ID})["scale"]
    assert scale["pt_per_ft"] == 9.0 and scale["source"] == "manual" and scale["confidence"] == 1.0
    assert scale["label"] == "calibrated 1:96"
    assert client.post(f"/ui/standin/sheets/{OTHER_ID}/calibrate", json={"p1": [1, 1], "p2": [1, 1], "real_ft": 5}).status_code == 422
    assert _measure(client, sheet=OTHER_ID).json()["measurement"]["quantity"] == pytest.approx(96.0)  # measurable now


def test_standin_create_condition(client, mock_db):
    r = client.post(f"/ui/standin/jobs/{JOB_ID}/conditions", json={
        "type_tag": "SB2", "color": "#123456", "name": "Demising", "studs": {"size_in": 6, "gauge": None, "spacing_in": 16},
        "layers_each_side": [{"thickness_in": 0.625, "board_type": "type_x"}], "sides": 2, "bogus": "dropped"})
    assert r.status_code == 200
    doc = mock_db.conditions.find_one({"_id": ObjectId(r.json()["id"])})
    assert doc["type_tag"] == "SB2" and doc["color"] == "#123456" and doc["job_id"] == str(JOB_ID) and "bogus" not in doc
    assert client.post(f"/ui/standin/jobs/{JOB_ID}/conditions", json={"name": "no tag"}).status_code == 422
    assert client.post(f"/ui/standin/jobs/{ObjectId()}/conditions", json={"type_tag": "X1"}).status_code == 404


# ---- the page and its bootstrap ----
def _boot(html: str) -> dict:
    m = re.search(r"window\.TAKEOFF = (\{.*?\});</script>", html, re.S)
    assert m, "no bootstrap"
    return json.loads(m.group(1).replace("\\u0027", "'").replace("\\u003c", "<").replace("\\u003e", ">").replace("\\u0026", "&"))


def test_takeoff_bootstrap(client):
    boot = _boot(client.get(f"/jobs/{JOB_ID}/takeoff").text)
    assert boot["sheet"] == str(PLAN_ID)  # first floor plan with an image
    by_no = {s["sheet_no"]: s for s in boot["sheets"]}
    assert by_no["A-101"]["discipline"] == {"code": "A", "name": "Architectural"}
    assert by_no["A-101"]["scale"]["pt_per_ft"] == PT_PER_FT and by_no["S-201"]["scale"] is None
    conds = {c["type_tag"]: c for c in boot["conditions"]}
    assert conds["A1"]["name"].startswith('A1 - 3-5/8" 20ga @ 16"') and conds["A1"]["color"].startswith("#")
    assert conds["A1"]["color"] != conds["SA4"]["color"]
    assert conds["SA4"]["name"] == 'SA4 - 3-5/8" (per specs) - 1L 5/8" Type X e.s.'  # gauge and spacing are null
    assert conds["A1"]["source"]["sheet"] == str(SHEET_PT_ID) and conds["A1"]["source"]["bbox"]


def test_takeoff_query_params(client):
    boot = _boot(client.get(f"/jobs/{JOB_ID}/takeoff?sheet={OTHER_ID}&cond={CONDITION_ID}").text)
    assert boot["sheet"] == str(OTHER_ID) and boot["cond"] == str(CONDITION_ID) and boot["focus"] is None
    boot = _boot(client.get(f"/jobs/{JOB_ID}/takeoff?focus={CONDITION_ID}").text)  # a condition's source: its sheet and box
    assert boot["sheet"] == str(SHEET_PT_ID)
    assert boot["focus"] == {"condition": str(CONDITION_ID), "tag": "A1", "sheet": str(SHEET_PT_ID), "bbox": [612.0, 140.5, 790.2, 260.0]}
    assert _boot(client.get(f"/jobs/{JOB_ID}/takeoff?sheet=bogus&cond=bogus").text)["cond"] is None


def test_takeoff_page_without_sheets(client, mock_db):
    empty = ObjectId()
    mock_db.jobs.insert_one({"_id": empty, "name": "Empty", "status": "ready", "created_at": AT})
    boot = _boot(client.get(f"/jobs/{empty}/takeoff").text)
    assert boot["sheet"] is None and boot["sheets"] == [] and boot["conditions"] == []
    assert client.get(f"/jobs/{ObjectId()}/takeoff").status_code == 404


def test_job_data_and_sheet_routes(client):
    data = client.get(f"/ui/jobs/{JOB_ID}/data").json()
    assert data["job"]["id"] == str(JOB_ID) and len(data["sheets"]) == 3 and len(data["conditions"]) == 2
    assert client.get(f"/ui/sheets/{PLAN_ID}").json()["scale"]["pt_per_ft"] == PT_PER_FT
    assert client.get(f"/ui/sheets/{ObjectId()}").status_code == 404


def test_conditions_page_copes_with_null_gauge(client):
    html = client.get(f"/jobs/{JOB_ID}/conditions").text
    assert "gauge, o.c. per specs" in html and "None" not in html.split("<table", 1)[1].split("</table>")[0]


def test_conditions_page_links_to_the_takeoff(client):
    html = client.get(f"/jobs/{JOB_ID}/conditions").text
    assert f"/takeoff?sheet={SHEET_PT_ID}&amp;focus={CONDITION_ID}" in html or f"/takeoff?sheet={SHEET_PT_ID}&focus={CONDITION_ID}" in html


# ---- helpers ----
def test_discipline():
    assert discipline("A-101")["code"] == "A" and discipline("A003")["name"] == "Architectural"
    assert discipline("M-201")["name"] == "Mechanical" and discipline("P-001")["name"] == "Plumbing"
    assert discipline(None) == {"code": "·", "name": "Other"} and discipline("")["code"] == "·"
    assert discipline("ZZ9")["name"] == "Z"  # unknown letters group under themselves


def test_condition_names_and_colors():
    c = {"type_tag": "A1", "studs": {"size_in": 3.625, "gauge": 20, "spacing_in": 16}, "sides": 2,
         "layers_each_side": [{"thickness_in": 0.625, "board_type": "type_x"}]}
    assert condition_name(c) == 'A1 - 3-5/8" 20ga @ 16" - 1L 5/8" Type X e.s.'
    assert stud_text({"size_in": 3.625, "gauge": 20, "spacing_in": 16}) == '3-5/8" 20 ga @ 16" o.c.'
    assert stud_text({"size_in": 6, "gauge": None, "spacing_in": 24}) == '6" @ 24" o.c. · gauge per specs'
    views = condition_views([{**c, "id": "b", "type_tag": "B1"}, {**c, "id": "a"}, {**c, "id": "c", "color": "#abcdef"}])
    assert [v["type_tag"] for v in views] == ["A1", "B1", "A1"] or len({v["color"] for v in views}) == 3
    assert len({v["color"] for v in views}) == 3 and "#abcdef" in {v["color"] for v in views}
    assert next_color(["#e8590c"]) != "#e8590c"


# ---- the shipped JS ----
NODE = shutil.which("node")


@pytest.mark.skipif(not NODE, reason="node isn't installed")
@pytest.mark.parametrize("name", ["api.js", "lib.js", "job.js", "takeoff.js"])
def test_js_parses(name):
    r = subprocess.run([NODE, "--check", str(ROOT / "ui" / "static" / name)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(not NODE, reason="node isn't installed")
def test_lib_js_helpers():
    script = """
      global.window = global; require(%s); const w = global;
      console.log(JSON.stringify([
        w.parseLength("24'-6\\""), w.parseLength("24' 6 1/2\\""), w.parseLength("24.5"), w.parseLength("300\\""), w.parseLength("7500mm"),
        w.parseLength("abc"), w.parseLength(""), w.fmtFtIn(48.25), w.fmtFtIn(0.6), w.discipline("A003").code,
        w.groupSheets([{sheet_no: "M-1"}, {sheet_no: "A-1"}, {sheet_no: "G-1"}, {sheet_no: null}]).map(g => g.code)]));
    """ % json.dumps(str(ROOT / "ui" / "static" / "lib.js"))
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    ft = json.loads(out)
    assert ft[0] == 24.5 and ft[1] == pytest.approx(24.5417, abs=1e-3) and ft[2] == 24.5 and ft[3] == 25
    assert ft[4] == pytest.approx(24.6063, abs=1e-3) and ft[5] is None and ft[6] is None
    assert ft[7] == "48' 3\"" and ft[8] == "0' 7\"" and ft[9] == "A"
    assert ft[10] == ["G", "A", "M", "·"]  # plan-set order, unnumbered sheets last
