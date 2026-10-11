"""Click-to-measure back end: quantities from the sheet scale, undo/delete, quantities by condition, order from takeoff."""
import pytest

from pipeline import orders, takeoff
from tests.pipeline.conftest import add_condition, add_sheet, by_id, new_id

WALL = [[400.0, 500.0], [1264.0, 500.0]]  # 864 pt: 96 LF at 1/8" = 1'-0" (9 pt/ft)


def measure(db, job, sheet, cond, kind="linear", points=WALL, **kw):
    return takeoff.add_measurement(job, sheet, cond, kind, points, **kw)


def test_864_pt_at_9_pt_per_ft_is_96_lf(db, job):
    sheet, cond = add_sheet(db, job), add_condition(db, job)

    doc = by_id(db.measurements, measure(db, job, sheet, cond, height_ft=10))

    assert (doc["quantity"], doc["unit"], doc["kind"], doc["pt_per_ft"], doc["height_ft"]) == (96.0, "lf", "linear", 9.0, 10)
    assert (doc["job_id"], doc["sheet"], doc["condition_id"], doc["points"]) == (job, sheet, cond, WALL) and doc["created_at"]


def test_linear_sums_every_segment_and_scales_by_the_sheet(db, job):
    cond = add_condition(db, job)
    quarter = add_sheet(db, job, pt_per_ft=18.0, page=1)  # 1/4" = 1'-0"
    eighth = add_sheet(db, job, pt_per_ft=9.0, page=2)
    path = [[0.0, 0.0], [90.0, 0.0], [90.0, 120.0]]  # 90 + 120 pt

    assert by_id(db.measurements, measure(db, job, eighth, cond, points=path))["quantity"] == pytest.approx(210 / 9)
    assert by_id(db.measurements, measure(db, job, quarter, cond, points=path))["quantity"] == pytest.approx(210 / 18)


def test_area_is_the_shoelace_area_over_pt_per_ft_squared(db, job):
    sheet, cond = add_sheet(db, job), add_condition(db, job)
    square = lambda side: [[100.0, 100.0], [100.0 + side, 100.0], [100.0 + side, 100.0 + side], [100.0, 100.0 + side]]

    two_pt = by_id(db.measurements, measure(db, job, sheet, cond, "area", square(2)))
    two_ft = by_id(db.measurements, measure(db, job, sheet, cond, "area", square(18)))  # 18 pt = 2 ft at 9 pt/ft
    clockwise = by_id(db.measurements, measure(db, job, sheet, cond, "area", square(18)[::-1]))
    triangle = by_id(db.measurements, measure(db, job, sheet, cond, "area", [[0.0, 0.0], [90.0, 0.0], [0.0, 90.0]]))

    assert (two_pt["quantity"], two_pt["unit"]) == (pytest.approx(4 / 81, abs=1e-6), "sf")  # 4 pt^2 / 81
    assert two_ft["quantity"] == pytest.approx(4.0)
    assert clockwise["quantity"] == pytest.approx(4.0)  # winding order doesn't matter
    assert triangle["quantity"] == pytest.approx(50.0)  # 10 ft x 10 ft / 2


def test_count_is_the_number_of_points(db, job):
    doc = by_id(db.measurements, measure(db, job, add_sheet(db, job), add_condition(db, job), "count", [[10.0, 10.0], [20.0, 20.0], [30.0, 30.0]]))
    assert (doc["quantity"], doc["unit"]) == (3.0, "ea")


def test_a_sheet_without_a_scale_is_a_clear_error(db, job, client):
    sheet, cond = add_sheet(db, job, pt_per_ft=None), add_condition(db, job)

    with pytest.raises(takeoff.ScaleMissing, match="set or calibrate the sheet scale first"):
        measure(db, job, sheet, cond)
    r = client.post("/api/measurements", json={"job_id": job, "sheet": sheet, "condition_id": cond, "kind": "linear", "points": WALL})

    assert r.status_code == 422 and "set or calibrate the sheet scale first" in r.json()["detail"]
    assert db.measurements.count_documents({"job_id": job}) == 0 and db.review_queue.count_documents({"doc.job_id": job}) == 0


def test_bad_measurements_are_refused(db, job):
    sheet, cond = add_sheet(db, job), add_condition(db, job)
    with pytest.raises(ValueError, match="at least 2 points"):
        measure(db, job, sheet, cond, points=[[1.0, 1.0]])
    with pytest.raises(ValueError, match="at least 3 points"):
        measure(db, job, sheet, cond, "area", WALL)
    with pytest.raises(ValueError, match="kind"):
        measure(db, job, sheet, cond, "volume")
    with pytest.raises(ValueError, match="points"):
        measure(db, job, sheet, cond, points=[["a", "b"], [1, 2]])
    with pytest.raises(ValueError, match="maximum"):  # off the sheet
        measure(db, job, sheet, cond, points=[[0.0, 0.0], [9000.0, 0.0]])
    with pytest.raises(ValueError, match="same job"):
        measure(db, new_id(), sheet, cond)
    with pytest.raises(LookupError, match="sheet"):
        measure(db, job, new_id(), cond)
    with pytest.raises(LookupError, match="condition"):
        measure(db, job, sheet, new_id())
    assert db.measurements.count_documents({"job_id": job}) == 0


def test_list_delete_and_undo(db, job):
    sheet, other_sheet, cond = add_sheet(db, job, page=1), add_sheet(db, job, page=2), add_condition(db, job)
    first = measure(db, job, sheet, cond)
    second = measure(db, job, sheet, cond, "count", [[5.0, 5.0]])
    elsewhere = measure(db, job, other_sheet, cond)

    assert [str(m["_id"]) for m in takeoff.list_measurements(sheet)] == [first, second]

    undone = takeoff.undo_last(sheet)
    assert str(undone["_id"]) == second and undone["kind"] == "count"
    assert [str(m["_id"]) for m in takeoff.list_measurements(sheet)] == [first]

    assert takeoff.delete_measurement(first) is True and takeoff.delete_measurement(first) is False
    assert takeoff.delete_measurement("nope") is False
    assert takeoff.undo_last(sheet) is None
    assert [str(m["_id"]) for m in takeoff.list_measurements(other_sheet)] == [elsewhere]


def test_quantities_group_two_conditions_by_unit_sorted_by_type_tag(db, job):
    sheet = add_sheet(db, job)
    a10 = add_condition(db, job, type_tag="A10", name='A10 - 3-5/8" 20ga @ 16"', color="#e6194b")
    a2 = add_condition(db, job, type_tag="A2", name='A2 - 6" 20ga @ 24"', color="#3cb44b")
    other_job = new_id()
    for cond, kind, points in (
        (a10, "linear", WALL),  # 96 LF
        (a10, "linear", [[0.0, 0.0], [49.5, 0.0]]),  # 5.5 LF
        (a10, "count", [[1.0, 1.0], [2.0, 2.0]]),  # 2 EA
        (a2, "area", [[0.0, 0.0], [90.0, 0.0], [90.0, 90.0], [0.0, 90.0]]),  # 100 SF
        (a2, "linear", [[0.0, 0.0], [0.0, 180.0]]),  # 20 LF
    ):
        measure(db, job, sheet, cond, kind, points)
    db.measurements.insert_one({"job_id": other_job, "sheet": sheet, "condition_id": a10, "kind": "linear", "points": WALL, "quantity": 999.0, "unit": "lf"})
    try:
        rows = takeoff.quantities(job)
    finally:
        db.measurements.delete_many({"job_id": other_job})

    assert [(r["type_tag"], r["unit"], r["quantity"], r["measurements"]) for r in rows] == [
        ("A2", "lf", 20.0, 1),  # A2 before A10, and within a condition by unit
        ("A2", "sf", 100.0, 1),
        ("A10", "ea", 2.0, 1),
        ("A10", "lf", 101.5, 2),
    ]
    assert rows[0]["condition_id"] == a2 and rows[0]["name"] == 'A2 - 6" 20ga @ 24"' and rows[0]["color"] == "#3cb44b"
    assert rows[3]["condition_id"] == a10 and rows[3]["color"] == "#e6194b"
    assert takeoff.quantities(new_id()) == []


def test_quantities_keep_a_row_for_a_condition_that_was_deleted(db, job):
    sheet, cond = add_sheet(db, job), add_condition(db, job)
    measure(db, job, sheet, cond)
    db.conditions.delete_one({"_id": by_id(db.conditions, cond)["_id"]})

    (row,) = takeoff.quantities(job)

    assert row["condition_id"] == cond and row["quantity"] == 96.0 and row["type_tag"] is None and row["name"] is None


# --- order from takeoff -----------------------------------------------------------------------------------------

def test_build_from_takeoff_gives_a_priced_order(db, job):
    sheet, cond = add_sheet(db, job), add_condition(db, job)  # the sample A1: height_ft 10
    measure(db, job, sheet, cond)  # 96 LF
    measure(db, job, sheet, cond, points=[[0.0, 0.0], [36.0, 0.0]])  # + 4 LF
    measure(db, job, sheet, cond, "count", [[1.0, 1.0]])  # not linear footage

    order = by_id(db.orders, orders.build_from_takeoff(job, cond))

    assert (order["lf"], order["height_ft"], order["condition_id"], order["job_id"]) == (100.0, 10, cond, job)
    assert order["total"] > 0 and orders.unpriced(order) == []
    assert order["total"] == pytest.approx(sum(l["qty"] * l["unit_cost"] for l in order["lines"]), abs=0.005)
    assert order["guard"]["over"] is False


def test_height_comes_from_the_condition_then_the_measurements_then_ten(db, job):
    sheet = add_sheet(db, job)
    on_condition = add_condition(db, job, height_ft=9)
    from_measurements = add_condition(db, job, type_tag="B1", height_ft=None)
    nowhere = add_condition(db, job, type_tag="C1", height_ft=None)
    measure(db, job, sheet, on_condition, height_ft=14)  # the condition's own height wins
    measure(db, job, sheet, from_measurements, height_ft=12)  # 96 LF at 12 ft
    measure(db, job, sheet, from_measurements, points=[[0.0, 0.0], [288.0, 0.0]], height_ft=8)  # 32 LF at 8 ft: LF-weighted mean 11
    measure(db, job, sheet, nowhere)

    heights = {c: by_id(db.orders, orders.build_from_takeoff(job, c))["height_ft"] for c in (on_condition, from_measurements, nowhere)}

    assert heights == {on_condition: 9, from_measurements: pytest.approx(11.0), nowhere: 10}


def test_build_from_takeoff_needs_measured_lf(db, job, client):
    sheet, cond = add_sheet(db, job), add_condition(db, job)
    measure(db, job, sheet, cond, "count", [[1.0, 1.0]])
    with pytest.raises(ValueError, match="measure its walls first"):
        orders.build_from_takeoff(job, cond)
    with pytest.raises(LookupError):
        orders.build_from_takeoff(job, new_id())
    assert client.post(f"/api/jobs/{job}/conditions/{cond}/order").status_code == 422
    assert client.post(f"/api/jobs/{job}/conditions/{new_id()}/order").status_code == 404
    assert db.orders.count_documents({"job_id": job}) == 0


# --- routes -----------------------------------------------------------------------------------------------------

def test_measurement_routes(db, job, client):
    sheet, cond = add_sheet(db, job), add_condition(db, job, type_tag="A1", name="A1 wall", color="#4363d8")
    body = {"job_id": job, "condition_id": cond, "kind": "linear", "points": WALL, "height_ft": 10}

    first = client.post("/api/measurements", json={**body, "sheet": sheet})
    second = client.post("/api/measurements", json={**body, "sheet_id": sheet, "points": [[0, 0], [90, 0]]})  # ints, `sheet_id` alias

    assert first.status_code == second.status_code == 200, first.text + second.text
    assert (first.json()["quantity"], first.json()["unit"], first.json()["id"]) == (96.0, "lf", first.json()["_id"])
    assert second.json()["quantity"] == pytest.approx(10.0)
    assert [m["id"] for m in client.get(f"/api/sheets/{sheet}/measurements").json()] == [first.json()["id"], second.json()["id"]]
    assert client.get(f"/api/sheets/{new_id()}/measurements").json() == []

    qty = client.get(f"/api/jobs/{job}/quantities").json()
    assert [(r["type_tag"], r["unit"], r["quantity"], r["name"], r["color"]) for r in qty] == [("A1", "lf", 106.0, "A1 wall", "#4363d8")]

    order = client.post(f"/api/jobs/{job}/conditions/{cond}/order")
    assert order.status_code == 200, order.text
    assert order.json()["total"] > 0 and order.json()["unpriced"] == [] and order.json()["order"]["lf"] == 106.0
    assert order.json()["order_id"] == order.json()["id"]

    undone = client.post(f"/api/sheets/{sheet}/measurements/undo")
    assert undone.json()["deleted"] == second.json()["id"] and undone.json()["measurement"]["quantity"] == pytest.approx(10.0)
    assert client.delete(f"/api/measurements/{first.json()['id']}").json() == {"deleted": first.json()["id"]}
    assert client.delete(f"/api/measurements/{first.json()['id']}").status_code == 404
    assert client.post(f"/api/sheets/{sheet}/measurements/undo").status_code == 404


def test_measurement_route_errors(db, job, client):
    sheet, cond = add_sheet(db, job), add_condition(db, job)
    body = {"job_id": job, "sheet": sheet, "condition_id": cond, "kind": "linear", "points": WALL}
    assert client.post("/api/measurements", json={**body, "sheet": new_id()}).status_code == 404
    assert client.post("/api/measurements", json={**body, "condition_id": new_id()}).status_code == 404
    assert client.post("/api/measurements", json={**body, "kind": "volume"}).status_code == 422
    assert client.post("/api/measurements", json={**body, "points": [[1.0, 1.0]]}).status_code == 422
    assert client.post("/api/measurements", json={k: v for k, v in body.items() if k != "sheet"}).status_code == 422
    assert db.measurements.count_documents({"job_id": job}) == 0
