"""Orders: priced inside MongoDB ($lookup + $merge), the over-purchase guard, and the PATCH that re-runs it."""
import importlib.machinery
import sys
import types

import pytest

from app.db import now
from pipeline import orders
from tests.pipeline.conftest import add_condition, by_id, new_id


def line(item, qty, computed, waste, unit_cost=2.0, unit="ea"):
    return {"item": item, "qty": qty, "unit": unit, "unit_cost": unit_cost, "computed_qty": computed, "waste": waste}


def insert_order(db, job, lines, **extra):
    doc = {"job_id": job, "condition_id": new_id(), "lf": 100, "height_ft": 10, "lines": lines, "total": 0, "supplier_email": None, "sent_at": None}
    return str(db.orders.insert_one({**doc, **extra}).inserted_id)


# --- build ------------------------------------------------------------------------------------------------------

def test_build_gives_a_priced_order(db, job):
    cid = add_condition(db, job)

    order_id = orders.build(job, cid, 100, 10)

    order = by_id(db.orders, order_id)
    assert (order["job_id"], order["condition_id"], order["lf"], order["height_ft"]) == (job, cid, 100, 10)
    assert order["sent_at"] is None and order["supplier_email"] is None and order["created_at"]
    assert order["lines"] and all(isinstance(l["unit_cost"], (int, float)) for l in order["lines"])
    assert order["total"] > 0
    assert order["total"] == pytest.approx(sum(l["qty"] * l["unit_cost"] for l in order["lines"]), abs=0.005)
    assert not {"pb", "i"} & set(order) and not any({"pb", "i"} & set(l) for l in order["lines"])
    by_item = {l["item"]: l for l in order["lines"]}
    assert by_item["stud_3.625_20ga_10ft"]["unit_cost"] == 10.15 and by_item["board_0.625_type_x_4x10"]["unit_cost"] == 21.6
    assert order["guard"] == {"over": False, "items_over": []}  # fresh math never trips its own guard
    assert orders.unpriced(order) == []


def test_unknown_items_stay_unpriced_and_are_listed(db, job, client):
    cid = add_condition(db, job, studs={"size_in": 3.625, "gauge": 22, "spacing_in": 16})  # no 22 ga in the price book

    r = client.post("/api/orders", json={"job_id": job, "condition_id": cid, "lf": 100, "height_ft": 10})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unpriced"] == ["stud_3.625_22ga_10ft", "track_3.625_22ga_10ft"]
    order = by_id(db.orders, body["order_id"])
    unknown = [l for l in order["lines"] if l["item"] in body["unpriced"]]
    assert len(unknown) == 2 and all(l["unit_cost"] is None for l in unknown)
    priced = [l for l in order["lines"] if l["unit_cost"] is not None]
    assert order["total"] == pytest.approx(sum(l["qty"] * l["unit_cost"] for l in priced), abs=0.005) and order["total"] > 0
    assert body["order_id"] == body["id"] == str(order["_id"]) and body["total"] == order["total"]
    assert client.get(f"/api/orders/{body['order_id']}").json()["unpriced"] == body["unpriced"]


def test_build_uses_orders_math_once_it_is_importable(db, job, monkeypatch):
    fake = types.ModuleType("orders.math")
    fake.__spec__ = importlib.machinery.ModuleSpec("orders.math", None)
    fake.lines_for = lambda condition, lf, height_ft: [
        {"item": "mud_all_purpose", "qty": 3, "unit": "box", "computed_qty": 3, "waste": 0},
        {"item": "tape_paper_500ft", "qty": 2, "unit": "roll", "computed_qty": 2, "waste": 0},
    ]
    monkeypatch.setitem(sys.modules, "orders.math", fake)
    cid = add_condition(db, job)

    order = by_id(db.orders, orders.build(job, cid, 50, 9))

    assert [l["item"] for l in order["lines"]] == ["mud_all_purpose", "tape_paper_500ft"]
    assert order["total"] == pytest.approx(3 * 22.0 + 2 * 6.0)


def test_build_rejects_bad_input(db, job):
    cid = add_condition(db, job)
    with pytest.raises(LookupError):
        orders.build(job, new_id(), 100, 10)
    with pytest.raises(LookupError):
        orders.build(job, "not-an-id", 100, 10)
    with pytest.raises(ValueError, match="another job"):
        orders.build(new_id(), cid, 100, 10)
    with pytest.raises(ValueError, match="positive"):
        orders.build(job, cid, 0, 10)
    assert db.orders.count_documents({"job_id": job}) == 0


def test_build_api_errors(db, job, client):
    cid = add_condition(db, job)
    assert client.post("/api/orders", json={"job_id": job, "condition_id": new_id(), "lf": 10, "height_ft": 10}).status_code == 404
    assert client.post("/api/orders", json={"job_id": job, "condition_id": cid, "lf": -5, "height_ft": 10}).status_code == 422
    assert client.get(f"/api/orders/{new_id()}").status_code == 404
    assert client.get("/api/orders/nope").status_code == 404


# --- guard ------------------------------------------------------------------------------------------------------

def test_a_padded_line_trips_the_guard_and_an_unpadded_one_does_not(db, job):
    order_id = insert_order(db, job, [
        line("stud", 80, 76, 0.05),  # 76 x 1.05 = 79.8: bought in whole studs, 80 is the allowance
        line("board", 55, 50, 0.10),  # 50 x 1.1 is 55.00000000000001 in floating point; 55 still passes
        line("track", 21, 20, 0.05),
        {"item": "loose", "qty": 99, "unit": "ea"},  # no takeoff to compare with: not flagged
    ])

    assert orders.guard(order_id) == {"over": False, "items_over": []}
    assert by_id(db.orders, order_id)["guard"] == {"over": False, "items_over": []}

    db.orders.update_one({"_id": by_id(db.orders, order_id)["_id"]}, {"$set": {"lines.1.qty": 56, "lines.2.qty": 22}})
    assert orders.guard(order_id) == {"over": True, "items_over": ["board", "track"]}


def test_guard_unknown_order(db):
    with pytest.raises(LookupError):
        orders.guard(new_id())
    with pytest.raises(LookupError):
        orders.guard("nope")


def test_patching_a_line_reruns_the_guard_and_the_total(db, job, client):
    cid = add_condition(db, job)
    built = client.post("/api/orders", json={"job_id": job, "condition_id": cid, "lf": 100, "height_ft": 10}).json()
    oid_, total = built["order_id"], built["total"]
    board = next(i for i, l in enumerate(built["order"]["lines"]) if l["item"].startswith("board_"))
    original = built["order"]["lines"][board]
    assert built["guard"] == {"over": False, "items_over": []}

    padded = client.patch(f"/api/orders/{oid_}/lines/{board}", json={"qty": original["qty"] * 2})
    assert padded.status_code == 200, padded.text
    view = padded.json()
    assert view["guard"] == {"over": True, "items_over": [original["item"]]}
    assert view["total"] == pytest.approx(total + original["qty"] * original["unit_cost"], abs=0.01)
    assert view["lines"][board]["qty"] == original["qty"] * 2

    restored = client.patch(f"/api/orders/{oid_}/lines/{board}", json={"qty": original["qty"]}).json()
    assert restored["guard"] == {"over": False, "items_over": []} and restored["total"] == pytest.approx(total, abs=0.01)
    assert client.post(f"/api/orders/{oid_}/guard").json() == {"over": False, "items_over": []}


def test_patch_errors(db, job, client):
    order_id = insert_order(db, job, [line("stud", 80, 76, 0.05)])
    assert client.patch(f"/api/orders/{order_id}/lines/1", json={"qty": 5}).status_code == 404  # no such line
    assert client.patch(f"/api/orders/{order_id}/lines/0", json={"qty": -1}).status_code == 422
    assert client.patch(f"/api/orders/{new_id()}/lines/0", json={"qty": 5}).status_code == 404
    assert client.post(f"/api/orders/{new_id()}/guard").status_code == 404
    db.orders.update_one({"_id": by_id(db.orders, order_id)["_id"]}, {"$set": {"sent_at": now()}})
    assert client.patch(f"/api/orders/{order_id}/lines/0", json={"qty": 5}).status_code == 409  # already sent
    assert by_id(db.orders, order_id)["lines"][0]["qty"] == 80
