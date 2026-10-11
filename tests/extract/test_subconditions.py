"""Sub-conditions: one wall type measured at different heights.

A sub-condition is a condition doc with `parent_id` and its own `height_ft`; measurements, quantities and orders
treat it like any other condition. A re-scan of the parent updates the children's assembly and nothing else.
"""
import dataclasses

import pytest
from bson import ObjectId

from app import jev, router
from app.db import get_db
from contracts.validate import validate
from extract import analyze, conditions
from extract.ingest import read_pages
from extract.partitions import partition_blocks
from tests.extract.test_analyze import keyword_jev, parsed

ASSEMBLY = ("type_tag", "studs", "layers_each_side", "sides", "materials", "source")


@pytest.fixture
def job(client, synthetic_pdf, monkeypatch):
    monkeypatch.setattr(jev, "choice", keyword_jev)
    monkeypatch.setattr(router, "run_task", parsed)
    with synthetic_pdf.open("rb") as f:
        r = client.post("/api/jobs", data={"name": "heights"}, files=[("files", ("synthetic.pdf", f, "application/pdf"))])
    return r.json()["job_id"]


def listed(client, job):
    return client.get(f"/api/jobs/{job}/conditions").json()


def a1(client, job):
    return next(c for c in listed(client, job) if c["type_tag"] == "A1")


def add(client, parent, height, **extra):
    return client.post(f"/api/conditions/{parent['id']}/subconditions", json={"height_ft": height, **extra})


def test_a_subcondition_copies_the_assembly_and_takes_its_own_height(client, job):
    parent = a1(client, job)
    r = add(client, parent, 12)
    assert r.status_code == 201, r.text
    sub = r.json()
    assert sub["id"] != parent["id"] and sub["parent_id"] == parent["id"] and sub["job_id"] == job
    assert sub["height_ft"] == 12
    assert sub["name"] == f"{parent['name']} @ 12'"
    for key in ASSEMBLY:
        assert sub[key] == parent[key], key
    assert sub["materials"], "the parent's materials come along, so the child can be ordered"
    assert sub["color"] and sub["color"] != parent["color"], "heights read apart on the drawing"
    assert "parent_id" not in parent or parent["parent_id"] is None
    assert [c["type_tag"] for c in listed(client, job)] == ["A1", "A1", "A2", "B1"]
    assert {c["id"] for c in listed(client, job)} >= {parent["id"], sub["id"]}


def test_a_fractional_height_and_a_typed_name(client, job):
    parent = a1(client, job)
    assert add(client, parent, 9.5).json()["name"] == f"{parent['name']} @ 9.5'"
    assert add(client, parent, 14, name="Atrium A1").json()["name"] == "Atrium A1"


def test_each_height_gets_its_own_colour(client, job):
    parent = a1(client, job)
    colours = {parent["color"], add(client, parent, 10).json()["color"], add(client, parent, 12).json()["color"]}
    assert len(colours) == 3


@pytest.mark.parametrize("height", [0, -4, 0.5, 61, "tall", None, True, []])
def test_a_height_outside_1_to_60_is_422(client, job, height):
    parent = a1(client, job)
    r = add(client, parent, height)
    assert r.status_code == 422, r.text
    assert len(listed(client, job)) == 3


def test_a_missing_height_is_422(client, job):
    parent = a1(client, job)
    assert client.post(f"/api/conditions/{parent['id']}/subconditions", json={}).status_code == 422


def test_the_limits_themselves_are_allowed(client, job):
    parent = a1(client, job)
    assert add(client, parent, 1).status_code == 201
    assert add(client, parent, 60).status_code == 201


def test_a_subcondition_has_no_subconditions_of_its_own(client, job):
    parent = a1(client, job)
    sub = add(client, parent, 12).json()
    r = add(client, sub, 16)
    assert r.status_code == 422 and "heights" in r.json()["detail"].lower()
    assert len(listed(client, job)) == 4


def test_unknown_parent_is_404(client, job):
    assert client.post("/api/conditions/000000000000000000000000/subconditions", json={"height_ft": 12}).status_code == 404
    assert client.post("/api/conditions/not-an-id/subconditions", json={"height_ft": 12}).status_code == 404


def test_deleting_a_parent_that_has_heights_is_409(client, job):
    parent = a1(client, job)
    sub = add(client, parent, 12).json()
    r = client.delete(f"/api/conditions/{parent['id']}")
    assert r.status_code == 409 and r.json()["detail"] == "Delete its heights first."
    assert len(listed(client, job)) == 4
    assert client.delete(f"/api/conditions/{sub['id']}").status_code == 204
    assert client.delete(f"/api/conditions/{parent['id']}").status_code == 204


def test_measurements_and_orders_use_the_subcondition_like_any_condition(client, job):
    """The child's own id carries the measurements, and its own height_ft is what an order is built from."""
    parent = a1(client, job)
    sub = add(client, parent, 14).json()
    get_db().measurements.insert_one({"job_id": job, "condition_id": sub["id"], "sheet": "s", "kind": "linear",
                                      "points": [[0, 0], [10, 0]], "quantity": 40, "unit": "lf"})
    r = client.delete(f"/api/conditions/{sub['id']}")
    assert r.status_code == 409 and "1 measurement" in r.json()["detail"]
    assert get_db().conditions.find_one({"parent_id": parent["id"]})["height_ft"] == 14


def test_the_schema_takes_parent_id_as_text_or_null():
    doc = {"type_tag": "A1", "studs": {"size_in": 3.625, "gauge": 20, "spacing_in": 16}, "sides": 2,
           "layers_each_side": [{"thickness_in": 0.625, "board_type": "type_x"}]}
    assert validate("conditions", doc) == []
    assert validate("conditions", {**doc, "parent_id": None}) == []
    assert validate("conditions", {**doc, "parent_id": "6acbf7840b3143b1fcfff0af", "height_ft": 12}) == []
    assert validate("conditions", {**doc, "parent_id": 5})


# ---- a re-scan of the parent ----
CHANGED = {"studs": {"size_in": 6, "gauge": 18, "spacing_in": 24}, "insulation": '3" mineral wool'}


def rescan_model(task, payload, validator_name=None, **kw):
    out = parsed(task, payload)
    if "REV 2" in payload:
        out["result"] = {**out["result"], **CHANGED}
    return out


def a501_blocks(synthetic_pdf):
    page = next(p for p in read_pages(synthetic_pdf) if any(s.text.strip() == "A1" for s in p.segs))
    return partition_blocks(page)


def revised(blocks):
    """The same blocks with A1's callout text changed, as a revised drawing would have."""
    return [dataclasses.replace(b, body=b.text.split("\n", 1)[1] + "\nREV 2") if b.tag == "A1" else b for b in blocks]


def test_a_rescan_updates_the_childs_assembly_and_keeps_what_is_its_own(mock_db, synthetic_pdf, monkeypatch):
    monkeypatch.setattr(router, "run_task", rescan_model)
    job, sheet = str(ObjectId()), {"_id": ObjectId(), "page": 6}
    blocks = a501_blocks(synthetic_pdf)
    analyze.parse_blocks(mock_db, job, sheet, blocks)
    parent = mock_db.conditions.find_one({"job_id": job, "type_tag": "A1"})
    sub = conditions.add_subcondition(mock_db, str(parent["_id"]), 12)
    mock_db.conditions.update_one({"_id": sub["_id"]}, {"$set": {"name": "A1 tall", "color": "#123456"}})
    assert sub["studs"]["size_in"] == 3.625 and validate("conditions", sub) == []

    analyze.parse_blocks(mock_db, job, sheet, revised(blocks))

    assert mock_db.conditions.count_documents({"job_id": job, "type_tag": "A1"}) == 2, "no third A1"
    new_parent = mock_db.conditions.find_one({"_id": parent["_id"]})
    child = mock_db.conditions.find_one({"parent_id": str(parent["_id"])})
    assert new_parent["studs"] == CHANGED["studs"] and "parent_id" not in new_parent
    assert child["_id"] == sub["_id"], "the child keeps its _id, so its measurements keep it"
    assert (child["height_ft"], child["name"], child["color"]) == (12, "A1 tall", "#123456")
    assert child["parent_id"] == str(parent["_id"])
    assert child["studs"] == CHANGED["studs"] and child["insulation"] == CHANGED["insulation"]
    assert child["materials"] == new_parent["materials"]
    assert any(m["key"] == "insulation" for m in child["materials"])
    assert child["source"] == new_parent["source"] and child["layers_each_side"] == new_parent["layers_each_side"]
    other = mock_db.conditions.find_one({"job_id": job, "type_tag": "A2"})
    assert other["studs"]["size_in"] == 3.625 and other.get("parent_id") is None


def test_a_rescan_drops_what_the_parent_lost_and_keeps_the_childs_products(mock_db, synthetic_pdf, monkeypatch):
    monkeypatch.setattr(router, "run_task", rescan_model)
    job, sheet = str(ObjectId()), {"_id": ObjectId(), "page": 6}
    blocks = a501_blocks(synthetic_pdf)
    analyze.parse_blocks(mock_db, job, sheet, revised(blocks))  # first read: with insulation
    parent = mock_db.conditions.find_one({"job_id": job, "type_tag": "A1"})
    assert parent["insulation"] == CHANGED["insulation"]
    sub = conditions.add_subcondition(mock_db, str(parent["_id"]), 10)
    mock_db.conditions.update_one({"_id": sub["_id"]}, {"$set": {"materials.0.product_id": "prod_stud_10ft"}})

    analyze.parse_blocks(mock_db, job, sheet, blocks)  # the revision is gone: no insulation, original studs

    child = mock_db.conditions.find_one({"_id": sub["_id"]})
    assert "insulation" not in child and child["studs"]["size_in"] == 3.625
    assert child["materials"][0]["key"] == "stud" and child["materials"][0]["product_id"] == "prod_stud_10ft"
    assert all(m["key"] != "insulation" for m in child["materials"])


def test_a_subcondition_is_never_the_rescans_upsert_target(mock_db, synthetic_pdf, monkeypatch):
    """The lookup is (job_id, type_tag, top level): a child holding the tag first must not be replaced."""
    monkeypatch.setattr(router, "run_task", parsed)
    job, sheet = str(ObjectId()), {"_id": ObjectId(), "page": 6}
    orphan = {"job_id": job, "type_tag": "A1", "studs": {"size_in": 2.5, "gauge": 25, "spacing_in": 24},
              "layers_each_side": [{"thickness_in": 0.5, "board_type": "regular"}], "sides": 1,
              "parent_id": str(ObjectId()), "height_ft": 16, "name": "A1 @ 16'", "color": "#abcdef"}
    mock_db.conditions.insert_one(dict(orphan))
    analyze.parse_blocks(mock_db, job, sheet, a501_blocks(synthetic_pdf))
    kept = mock_db.conditions.find_one({"parent_id": orphan["parent_id"]})
    assert {k: kept[k] for k in orphan} == orphan, "the child is untouched"
    tops = list(mock_db.conditions.find({"job_id": job, "type_tag": "A1", "parent_id": None}))
    assert len(tops) == 1 and tops[0]["studs"]["size_in"] == 3.625


def test_editing_a_wall_types_build_rebuilds_its_materials_and_reaches_its_heights(client, job):
    parent = a1(client, job)
    sub = add(client, parent, 12).json()
    db = get_db()
    for c in (parent, sub):  # every material already has a product found for it
        mats = [{**m, "product_id": f"{c['id'][-4:]}-{m['key']}"} for m in c["materials"]]
        db.conditions.update_one({"_id": ObjectId(c["id"])}, {"$set": {"materials": mats}})
    before = {m["key"]: m for m in a1(client, job)["materials"]}

    r = client.patch(f"/api/conditions/{parent['id']}", json={"studs": {"size_in": 6}})
    assert r.status_code == 200, r.text
    edited = r.json()
    assert edited["studs"]["size_in"] == 6 and edited["name"].startswith("A1 - 6\"")
    rows = {m["key"]: m for m in edited["materials"]}
    assert set(rows) == set(before), "same materials, rebuilt in place"
    assert rows["stud"]["size_in"] == 6 and rows["stud"]["desc"].startswith('6"') and not rows["stud"].get("product_id")
    assert rows["track"]["size_in"] == 6 and not rows["track"].get("product_id")
    boards = [k for k in rows if rows[k]["role"] == "board"]
    assert boards and all(rows[k]["product_id"] == before[k]["product_id"] for k in boards), "an unchanged board keeps its product"
    for k, m in rows.items():
        if m["role"] not in ("stud", "track", "board", "insulation"):
            assert m == before[k], f"{k} came from the callout text and stays as it was"
    if "insulation" in rows:
        assert rows["insulation"]["size_in"] == 6 and not rows["insulation"].get("product_id")
    assert validate("conditions", {k: v for k, v in db.conditions.find_one({"_id": ObjectId(parent["id"])}).items() if k != "_id"}) == []

    child = next(c for c in listed(client, job) if c["id"] == sub["id"])
    assert child["studs"]["size_in"] == 6 and child["name"] == f"{edited['name']} @ 12'", "the height follows its wall type"
    child_rows = {m["key"]: m for m in child["materials"]}
    assert not child_rows["stud"].get("product_id")
    assert all(child_rows[k]["product_id"] == f"{sub['id'][-4:]}-{k}" for k in boards), "the height keeps its own products"
    assert child["color"] == sub["color"] and child["height_ft"] == 12


def test_a_height_edits_its_own_name_colour_and_height_but_not_the_build(client, job):
    parent = a1(client, job)
    sub = add(client, parent, 12).json()

    r = client.patch(f"/api/conditions/{sub['id']}", json={"studs": {"size_in": 6}})
    assert r.status_code == 422 and "wall type" in r.json()["detail"]
    r = client.patch(f"/api/conditions/{sub['id']}", json={"type_tag": "A1", "height_ft": 14, "name": "Tall corridor", "color": "#123456"})
    assert r.status_code == 200, r.text  # an unchanged build field is fine
    assert (r.json()["height_ft"], r.json()["name"], r.json()["color"]) == (14, "Tall corridor", "#123456")
    assert a1(client, job)["studs"] == parent["studs"]
