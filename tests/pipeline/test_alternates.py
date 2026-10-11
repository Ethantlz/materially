from bson import ObjectId
from fastapi.testclient import TestClient

from app.main import app
from pipeline import orders, takeoff
from projects import alternates as alt
from ui import api as ui_api


def _setup(db):
    job = str(ObjectId())
    sheet = db.sheets.insert_one({"job_id": job, "page": 1, "kind": "floor_plan", "scale": {"pt_per_ft": 9.0}}).inserted_id
    cond = db.conditions.insert_one({"job_id": job, "type_tag": "A1", "studs": {"size_in": 3.625, "gauge": 20, "spacing_in": 16},
                                     "layers_each_side": [{"thickness_in": 0.625, "board_type": "type_x"}], "sides": 2}).inserted_id
    return job, str(sheet), str(cond)


def test_quantities_are_scoped_to_the_alternate(mock_db, monkeypatch):
    monkeypatch.setenv("DB_NAME", mock_db.name)
    job, sheet, cond = _setup(mock_db)
    a1 = alt.create_alternate(job, "Alternate 1")
    takeoff.add_measurement(job, sheet, cond, "linear", [[0, 0], [900, 0]])                 # 100 LF, base
    takeoff.add_measurement(job, sheet, cond, "linear", [[0, 0], [450, 0]], alternate_id=a1)  # 50 LF, alternate 1
    base_lf = sum(r["quantity"] for r in takeoff.quantities(job) if r["unit"] == "lf")
    alt_lf = sum(r["quantity"] for r in takeoff.quantities(job, a1) if r["unit"] == "lf")
    assert (base_lf, alt_lf) == (100, 50)
    assert takeoff.linear_takeoff(job, cond, a1)[0] == 50
    stored = mock_db.measurements.find_one({"quantity": 100})
    assert stored["alternate_id"] == str(alt.ensure_base(job)["_id"])


def test_order_from_takeoff_uses_and_stores_the_alternate(mock_db, monkeypatch):
    monkeypatch.setenv("DB_NAME", mock_db.name)
    job, sheet, cond = _setup(mock_db)
    a1 = alt.create_alternate(job, "Alternate 1")
    takeoff.add_measurement(job, sheet, cond, "linear", [[0, 0], [450, 0]], alternate_id=a1)
    order = mock_db.orders.find_one({"_id": ObjectId(orders.build_from_takeoff(job, cond, a1))})
    assert order["lf"] == 50 and order["alternate_id"] == a1


def test_sheet_measurements_are_scoped(mock_db, monkeypatch):
    monkeypatch.setenv("DB_NAME", mock_db.name)
    job, sheet, cond = _setup(mock_db)
    a1 = alt.create_alternate(job, "Alternate 1")
    takeoff.add_measurement(job, sheet, cond, "linear", [[0, 0], [900, 0]])                   # 100 LF, base
    takeoff.add_measurement(job, sheet, cond, "linear", [[0, 0], [450, 0]], alternate_id=a1)  # 50 LF, alternate 1
    client = TestClient(app)
    base = client.get(f"/api/sheets/{sheet}/measurements")
    only_a1 = client.get(f"/api/sheets/{sheet}/measurements", params={"alternate_id": a1})
    assert base.status_code == only_a1.status_code == 200
    assert [m["quantity"] for m in base.json()] == [100]
    assert [m["quantity"] for m in only_a1.json()] == [50]


def test_undo_only_touches_the_current_alternate(mock_db, monkeypatch):
    monkeypatch.setenv("DB_NAME", mock_db.name)
    job, sheet, cond = _setup(mock_db)
    a1 = alt.create_alternate(job, "Alternate 1")
    base_id = takeoff.add_measurement(job, sheet, cond, "linear", [[0, 0], [900, 0]])                 # base, made first
    a1_id = takeoff.add_measurement(job, sheet, cond, "linear", [[0, 0], [450, 0]], alternate_id=a1)  # newest, alternate 1
    client = TestClient(app)
    undone = client.post(f"/api/sheets/{sheet}/measurements/undo")
    assert undone.status_code == 200 and undone.json()["deleted"] == base_id
    assert mock_db.measurements.count_documents({"_id": ObjectId(a1_id)}) == 1
    undone_a1 = client.post(f"/api/sheets/{sheet}/measurements/undo", params={"alternate_id": a1})
    assert undone_a1.status_code == 200 and undone_a1.json()["deleted"] == a1_id
    assert client.post(f"/api/sheets/{sheet}/measurements/undo").status_code == 404
    assert mock_db.measurements.count_documents({"sheet": sheet}) == 0


def test_condition_page_lf_counts_base_bid_only(mock_db, monkeypatch):
    monkeypatch.setenv("DB_NAME", mock_db.name)
    job, sheet, cond = _setup(mock_db)
    a1 = alt.create_alternate(job, "Alternate 1")
    takeoff.add_measurement(job, sheet, cond, "linear", [[0, 0], [900, 0]])                   # 100 LF, base
    takeoff.add_measurement(job, sheet, cond, "linear", [[0, 0], [450, 0]], alternate_id=a1)  # 50 LF, alternate 1
    measured = ui_api._measured(mock_db, job)
    assert measured[cond]["lf"] == 100 and measured[cond]["sf"] == 0 and measured[cond]["ea"] == 0
