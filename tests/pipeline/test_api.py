"""The EWO and draft routes (the order and cost routes are covered next to their logic)."""
from pipeline import ewo as ewo_mod
from tests.pipeline.conftest import by_id, new_id


def post_ewo(client, job, sheet, x=1290.0, y=902.0, **extra):
    return client.post("/api/ewos", json={"job_id": job, "sheet": sheet, "x": x, "y": y, "description": " Add a 4 ft wall at the alcove. ", **extra})


def test_post_ewo_maps_the_pin_to_a_room_and_routes_in_the_background(db, job, client, monkeypatch):
    routed = []
    monkeypatch.setattr(ewo_mod, "route", lambda ewo_id: routed.append(ewo_id))
    sheet = new_id()
    room = str(db.rooms.insert_one({"job_id": job, "sheet": sheet, "name": "EXAM", "number": "104", "label_pt": [1312.5, 884.0]}).inserted_id)

    r = post_ewo(client, job, sheet)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ewo_id"] == body["id"] and body["room_id"] == room and body["room"]["number"] == "104" and body["status"] == "new"
    assert routed == [body["ewo_id"]]
    doc = by_id(db.ewos, body["ewo_id"])
    assert doc["description"] == "Add a 4 ft wall at the alcove." and doc["pin"] == [1290.0, 902.0]
    assert [e["id"] for e in client.get("/api/ewos", params={"job_id": job}).json()] == [body["ewo_id"]]
    assert client.get("/api/ewos", params={"job_id": new_id()}).json() == []


def test_post_ewo_honors_the_foremans_room_choice(db, job, client, monkeypatch):
    monkeypatch.setattr(ewo_mod, "route", lambda ewo_id: None)
    sheet = new_id()
    db.rooms.insert_one({"job_id": job, "sheet": sheet, "name": "EXAM", "number": "104", "label_pt": [1312.5, 884.0]})
    chosen = str(db.rooms.insert_one({"job_id": job, "sheet": sheet, "name": "WAITING", "number": "101", "label_pt": [2500.0, 1800.0]}).inserted_id)

    assert post_ewo(client, job, sheet, room_id=chosen).json()["room_id"] == chosen
    assert post_ewo(client, job, sheet, room_id=new_id()).json()["room_id"] != chosen  # not a room here: $near decides
    assert post_ewo(client, job, sheet, x=4000.0, y=4000.0).json()["room_id"] is None


def test_post_ewo_validation(db, job, client, monkeypatch):
    monkeypatch.setattr(ewo_mod, "route", lambda ewo_id: None)
    assert post_ewo(client, job, new_id(), x=9000.0).status_code == 422  # off the sheet
    assert client.post("/api/ewos", json={"job_id": job, "sheet": new_id(), "x": 1, "y": 1, "description": ""}).status_code == 422
    assert client.post("/api/ewos", json={"job_id": job}).status_code == 422
    assert db.ewos.count_documents({"job_id": job}) == 0


def test_a_crashing_route_parks_the_ewo_in_review(db, job, client, monkeypatch):
    def boom(ewo_id):
        raise RuntimeError("atlas went away")

    monkeypatch.setattr(ewo_mod, "route", boom)

    body = post_ewo(client, job, new_id()).json()

    assert by_id(db.ewos, body["ewo_id"])["status"] == "review"
    row = db.review_queue.find_one({"doc.ewo_id": body["ewo_id"]})
    assert "atlas went away" in row["reason"] and row["doc"]["job_id"] == job


def test_route_endpoint(db, job, client, monkeypatch):
    ewo_id = ewo_mod.create(job, new_id(), 100.0, 100.0, "Add a wall.")

    def fake_route(wanted):
        if wanted != ewo_id:
            raise LookupError("no such ewo")
        return "d" * 24

    monkeypatch.setattr(ewo_mod, "route", fake_route)

    assert client.post(f"/api/ewos/{ewo_id}/route").json() == {"ewo_id": ewo_id, "draft_id": "d" * 24, "status": "new"}
    assert client.post(f"/api/ewos/{new_id()}/route").status_code == 404


def test_drafts_are_listed_newest_first_with_their_ewo_and_room(db, job, client):
    sheet = new_id()
    room = str(db.rooms.insert_one({"job_id": job, "sheet": sheet, "name": "EXAM", "number": "104", "label_pt": [1312.5, 884.0]}).inserted_id)
    ewo_id = ewo_mod.create(job, sheet, 1290.0, 902.0, "Add a 4 ft wall.")
    base = {"task": "price_extra", "line_items": [], "total": 0, "model": "m", "escalated": False, "routing": {}}
    first = str(db.ewo_drafts.insert_one({**base, "ewo_id": ewo_id, "job_id": job, "explanation": "first"}).inserted_id)
    second = str(db.ewo_drafts.insert_one({**base, "ewo_id": ewo_id, "explanation": "second, no job_id"}).inserted_id)  # found through its ewo
    db.ewo_drafts.insert_one({**base, "ewo_id": new_id(), "job_id": new_id(), "explanation": "another job"})
    try:
        drafts = client.get("/api/drafts", params={"job_id": job}).json()

        assert [d["id"] for d in drafts] == [second, first]
        assert drafts[0]["ewo"]["description"] == "Add a 4 ft wall." and drafts[0]["ewo"]["room_id"] == room
        assert drafts[0]["room"] == {"name": "EXAM", "number": "104"}
        assert client.get("/api/drafts").status_code == 422  # job_id is required
    finally:
        db.ewo_drafts.delete_many({"ewo_id": ewo_id})
        db.ewo_drafts.delete_many({"explanation": "another job"})
