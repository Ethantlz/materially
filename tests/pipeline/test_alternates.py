from bson import ObjectId

from pipeline import orders, takeoff
from projects import alternates as alt


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
