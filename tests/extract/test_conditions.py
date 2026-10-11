"""Conditions library: generated names, stable colours, and create / edit / delete / duplicate."""
import re

import pytest

from app import jev, router
from app.db import now
from extract import conditions
from tests.extract.helpers import forget_job
from tests.extract.test_analyze import keyword_jev, parsed

A1 = {"type_tag": "A1", "studs": {"size_in": 3.625, "gauge": 20, "spacing_in": 16},
      "layers_each_side": [{"thickness_in": 0.625, "board_type": "type_x"}], "sides": 2}


def test_name_follows_the_takeoff_shorthand():
    assert conditions.name_for(A1) == 'A1 - 3-5/8" 20ga @ 16" - 1L 5/8" Type X e.s.'


@pytest.mark.parametrize("condition,name", [
    ({**A1, "type_tag": "SA4", "studs": {"size_in": 3.625, "gauge": None, "spacing_in": 16}},
     'SA4 - 3-5/8" @ 16" - 1L 5/8" Type X e.s.'),
    ({**A1, "type_tag": "SH2", "studs": {"size_in": 2.5, "gauge": None, "spacing_in": None}, "sides": 1},
     'SH2 - 2-1/2" - 1L 5/8" Type X o.s.'),
    ({**A1, "studs": {"size_in": 6, "gauge": 18, "spacing_in": 24},
      "layers_each_side": [{"thickness_in": 0.625, "board_type": "type_x"}] * 2},
     'A1 - 6" 18ga @ 24" - 2L 5/8" Type X e.s.'),
    ({**A1, "layers_each_side": [{"thickness_in": 0.5, "board_type": "regular"},
                                 {"thickness_in": 0.625, "board_type": "moisture_resistant"}]},
     'A1 - 3-5/8" 20ga @ 16" - 1L 1/2" Regular + 1L 5/8" MR e.s.'),
])
def test_name_variants(condition, name):
    assert conditions.name_for(condition) == name


def test_colours_are_stable_per_tag_and_distinct_in_a_job(mock_db):
    tags = ["SA4", "SC2", "SC4", "SD4", "SH2", "SH4", "SH6", "A1", "A2", "B1"]
    picked = {}
    for tag in tags:
        picked[tag] = conditions.color_for(mock_db, "job", tag)
        mock_db.conditions.insert_one({"job_id": "job", "type_tag": tag, "color": picked[tag]})
    assert len(set(picked.values())) == len(tags), "no two conditions in a job share a colour while the palette lasts"
    assert all(re.fullmatch(r"#[0-9a-f]{6}", c) for c in picked.values())
    assert conditions.color_for(mock_db, "job", "SC2") == picked["SC2"], "a tag already in the job keeps its colour"
    assert conditions.color_for(mock_db, "other job", "A1") == conditions.PALETTE[conditions.preferred(("A1"))]


@pytest.fixture
def job(client, synthetic_pdf, monkeypatch):
    monkeypatch.setattr(jev, "choice", keyword_jev)
    monkeypatch.setattr(router, "run_task", parsed)
    with synthetic_pdf.open("rb") as f:
        r = client.post("/api/jobs", data={"name": "lib"}, files=[("files", ("synthetic.pdf", f, "application/pdf"))])
    return r.json()["job_id"]


def test_parsed_conditions_have_name_colour_source_routing(client, job):
    listed = client.get(f"/api/jobs/{job}/conditions").json()
    assert [c["name"] for c in listed] == [f'{t} - 3-5/8" 20ga @ 16" - 1L 5/8" Type X e.s.' for t in ("A1", "A2", "B1")]
    assert len({c["color"] for c in listed}) == 3
    assert all(c["source"]["sheet"] and len(c["source"]["bbox"]) == 4 and c["routing"]["model"] for c in listed)


def test_list_backfills_name_and_colour(client, job):
    from app.db import get_db

    get_db().conditions.update_many({"job_id": job}, {"$unset": {"name": "", "color": ""}})
    first = client.get(f"/api/jobs/{job}/conditions").json()
    assert all(c["name"] and c["color"] for c in first)
    assert client.get(f"/api/jobs/{job}/conditions").json() == first, "backfilled once, then stored"


def test_create_edit_duplicate_delete(client, job):
    r = client.post(f"/api/jobs/{job}/conditions", json={**A1, "type_tag": "C7", "fire_rating": 1})
    assert r.status_code == 201, r.text
    c7 = r.json()
    assert c7["name"].startswith('C7 - 3-5/8" 20ga') and c7["color"] and c7["routing"]["method"] == "manual"
    assert c7["job_id"] == job and c7["fire_rating"] == 1

    r = client.patch(f"/api/conditions/{c7['id']}", json={"studs": {"size_in": 6, "gauge": 18, "spacing_in": 16}})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == 'C7 - 6" 18ga @ 16" - 1L 5/8" Type X e.s.', "an auto name follows the edit"
    assert r.json()["color"] == c7["color"]
    r = client.patch(f"/api/conditions/{c7['id']}", json={"name": "Corridor wall"})
    assert r.json()["name"] == "Corridor wall"
    r = client.patch(f"/api/conditions/{c7['id']}", json={"sides": 1})
    assert r.json()["name"] == "Corridor wall", "a name someone typed is kept"

    r = client.post(f"/api/conditions/{c7['id']}/duplicate")
    assert r.status_code == 201, r.text
    copy = r.json()
    assert copy["id"] != c7["id"] and copy["name"] == "Corridor wall (copy)" and copy["color"] != c7["color"]
    assert copy["studs"] == {"size_in": 6, "gauge": 18, "spacing_in": 16} and copy["routing"]["duplicate_of"] == c7["id"]

    assert client.delete(f"/api/conditions/{copy['id']}").status_code == 204
    tags = [c["type_tag"] for c in client.get(f"/api/jobs/{job}/conditions").json()]
    assert tags == ["A1", "A2", "B1", "C7"]


def test_bad_edits_are_refused(client, job):
    c = client.get(f"/api/jobs/{job}/conditions").json()[0]
    r = client.patch(f"/api/conditions/{c['id']}", json={"studs": {"size_in": 3.625, "gauge": 19, "spacing_in": 16}})
    assert r.status_code == 422 and "gauge" in r.json()["detail"]
    assert client.patch(f"/api/conditions/{c['id']}", json={"job_id": "elsewhere"}).status_code == 422
    assert client.get(f"/api/jobs/{job}/conditions").json()[0]["studs"]["gauge"] == 20


def test_delete_refuses_a_condition_in_use(client, job):
    from app.db import get_db

    c = client.get(f"/api/jobs/{job}/conditions").json()[0]
    get_db().measurements.insert_one({"job_id": job, "condition_id": c["id"], "sheet": "s", "kind": "linear",
                                      "points": [[0, 0], [10, 0]], "quantity": 1, "unit": "lf"})
    r = client.delete(f"/api/conditions/{c['id']}")
    assert r.status_code == 409 and "1 measurement" in r.json()["detail"]


@pytest.mark.parametrize("call", ["patch", "delete", "duplicate"])
def test_unknown_condition_is_404(client, call):
    cid = "000000000000000000000000"
    r = {"patch": lambda: client.patch(f"/api/conditions/{cid}", json={"sides": 1}),
         "delete": lambda: client.delete(f"/api/conditions/{cid}"),
         "duplicate": lambda: client.post(f"/api/conditions/{cid}/duplicate")}[call]()
    assert r.status_code == 404


def test_create_on_unknown_job_is_404(client):
    assert client.post("/api/jobs/000000000000000000000000/conditions", json=A1).status_code == 404


def test_mongodb_rejects_a_bad_manual_condition_into_review(live_db):
    job_id = str(live_db.jobs.insert_one({"name": "manual condition test", "created_at": now(), "status": "ready"}).inserted_id)
    try:
        doc, reason = conditions.create(live_db, job_id, {**A1, "studs": {**A1["studs"], "gauge": 19}})
        assert doc is None and "validation" in reason.lower()
        review = live_db.review_queue.find_one({"source": "extract.condition", "doc.job_id": job_id})
        assert review["collection"] == "conditions"
    finally:
        forget_job(live_db, job_id)


def test_metric_conversions_snap_to_nominal_sizes():
    parsed_metric = {
        "type_tag": "P3",
        "studs": {"size_in": 3.622, "gauge": None, "spacing_in": 23.622},
        "layers_each_side": [{"thickness_in": 0.63, "board_type": "type_x"}, {"thickness_in": 0.748, "board_type": "regular"}],
    }
    out, changed = conditions.nominal(parsed_metric)
    assert out["studs"] == {"size_in": 3.625, "gauge": None, "spacing_in": 24}
    assert [layer["thickness_in"] for layer in out["layers_each_side"]] == [0.625, 0.75]
    assert changed == {"studs.size_in": 3.622, "studs.spacing_in": 23.622,
                       "layers_each_side.0.thickness_in": 0.63, "layers_each_side.1.thickness_in": 0.748}
    assert parsed_metric["studs"]["spacing_in"] == 23.622, "the model's answer is not changed in place"


def test_nominal_leaves_exact_and_odd_values_alone():
    out, changed = conditions.nominal(A1)
    assert out == A1 and changed == {}
    odd = {**A1, "studs": {"size_in": 3.5, "gauge": 20, "spacing_in": 19.2}}
    assert conditions.nominal(odd) == (odd, {})
    far = {**A1, "studs": {"size_in": 5.0, "gauge": 20, "spacing_in": 20}}  # not near any trade size: kept
    assert conditions.nominal(far) == (far, {})
    broken = {"type_tag": "SB2", "studs": {"size_in": None}, "layers_each_side": None}
    assert conditions.nominal(broken) == (broken, {})
