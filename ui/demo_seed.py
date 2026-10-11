"""Seed a viewable job so every screen has something on it before lanes 1 and 2 merge.

    $env:DB_NAME = "lane3"; infisical run --env=dev -- uv run python -m ui.demo_seed

1. Builds the synthetic 7-sheet ARCH D set and runs the real extract.ingest (names, scales, page images).
2. With keys: Jev sorts each sheet (asked twice, shuffled) and DeepSeek reads A1/A2/B1 on A-501 through
   run_task, so routing badges and the cost badge are real. Without keys it uses the drawing's values.
3. Rooms 101/102/103 on the plan sheets (label centres from the generator), one EWO routed and drafted live,
   a sample product sheet with a deliberate mismatch, an A1 order with a padded board line, and a bad
   condition written through insert_or_review so MongoDB's rejection shows in the review queue.
Docs that stand in for another lane's output carry `sample: true`.
"""
import sys

from app import jev
from app.db import get_db, insert_or_review, now, oid, to_review
from app.router import TaskFailed, run_task
from app.settings import get_settings
from contracts.samples import BAD_CONDITION
from contracts.schemas import SHEET_KINDS
from extract.ingest import ingest
from scripts.make_drawing_set import H, build

PARTITION_TEXT = '{tag}\n3 5/8" 20 GA STUDS @ 16" O.C.\n(1) LAYER 5/8" TYPE X GYP. BD. EACH SIDE'
DRAWING_CONDITION = {
    "studs": {"size_in": 3.625, "gauge": 20, "spacing_in": 16},
    "layers_each_side": [{"thickness_in": 0.625, "board_type": "type_x"}],
    "sides": 2,
}
ROOMS = [("OFFICE", "101"), ("CORRIDOR", "102"), ("STORAGE", "103")]
EWO_TEXT = "Owner added a 6 ft wall to split the office for a file room, full height, same as A1. 1 hr crew lift needed."
TASKS = {
    "price_extra": "New work not on the bid drawings that needs pricing as an extra.",
    "base_scope_check": "Work that may already be in the base contract scope.",
    "backcharge": "Damage or rework caused by another trade, to be charged back.",
}


def _live() -> bool:
    s = get_settings()
    return bool(s.openrouter_api_key and (s.litellm_api_key or s.luminal_api_key))


def _rooms_for(sheet: dict) -> list[dict]:
    """Room label centres from scripts/make_drawing_set.py, flipped to top-left PDF points."""
    views = 2 if sheet["sheet_no"] == "A-102" else 1
    out = []
    for v in range(views):
        x0, y0 = 120 + v * 1050, 360
        for r, (name, number) in enumerate(ROOMS):
            out.append({"job_id": sheet["job_id"], "sheet": sheet["id"], "name": name, "number": number,
                        "label_pt": [float(x0 + 160 + r * 280), float(H - (y0 + 754))]})
    return out


def seed(db, live: bool) -> str:
    data_dir = get_settings().data_dir
    job_id = str(db.jobs.insert_one({"name": "Materially Test Building (sample)", "created_at": now(),
                                     "status": "uploading", "pages_total": 0, "pages_named": 0, "pages_rendered": 0}).inserted_id)
    src = data_dir / job_id / "source"
    src.mkdir(parents=True, exist_ok=True)
    pdf = src / "materially-test-building.pdf"
    build(pdf)
    ingest(db, job_id, [pdf], data_dir)
    sheets = {s["sheet_no"]: {**s, "id": str(s["_id"])} for s in db.sheets.find({"job_id": job_id})}
    print(f"job {job_id}: {len(sheets)} sheets ingested")

    if live:  # sheet kinds by Jev, as lane 1 will do it
        for s in sheets.values():
            try:
                r = jev.choice(f"{s['sheet_no']} {s.get('title') or ''}", SHEET_KINDS, "Which kind of drawing sheet is this?", job_id=job_id)
            except jev.JevError as e:
                print("  jev failed:", e)
                continue
            upd = {"routing": r} | ({"kind": r["choice"]} if r["choice"] else {})
            db.sheets.update_one({"_id": s["_id"]}, {"$set": upd})
            if not r["choice"]:
                to_review(db, "extract.sheet_kind", {"job_id": job_id, "sheet_no": s["sheet_no"]}, "Jev's two answers disagreed")
        print("  sheet kinds routed by Jev")

    pt = sheets.get("A-501")
    for i, tag in enumerate(["A1", "A2", "B1"]):
        cond, routing = {"type_tag": tag, **DRAWING_CONDITION}, {"model": "drawing values", "escalated": False, "sample": True}
        if live:
            try:
                out = run_task("parse_partition", PARTITION_TEXT.format(tag=tag), "conditions", job_id=job_id)
                cond, routing = out["result"], {"task": "parse_partition", "model": out["model"], "escalated": out["escalated"]}
            except TaskFailed as e:
                to_review(db, "parse_partition", {"job_id": job_id, "type_tag": tag}, e.reason)
                continue
        source = {"sheet": pt["id"], "page": pt["page"], "bbox": [150.0 + i * 500, 270.0, 470.0 + i * 500, 385.0]} if pt else {}
        insert_or_review(db, "conditions", {**cond, "job_id": job_id, "source": source, "routing": routing}, "extract.partition")
    insert_or_review(db, "conditions", {**BAD_CONDITION, "job_id": job_id}, "extract.partition")  # rejected -> review_queue
    print(f"  {db.conditions.count_documents({'job_id': job_id})} conditions, bad parse sent to review")

    for no in ("A-101", "A-102"):
        if no in sheets:
            db.rooms.insert_many(_rooms_for(sheets[no]))
    a1 = db.conditions.find_one({"job_id": job_id, "type_tag": "A1"})
    if a1:
        cid = str(a1["_id"])
        db.product_data.insert_many([
            {"query_hash": f"sample-{job_id}-board-ok", "condition_id": cid, "component": "board", "sample": True,
             "url": "https://example.com/sample-5-8-type-x.pdf", "title": 'Sample: 5/8" Type X gypsum board data sheet',
             "attrs": {"thickness_in": 0.625, "board_type": "type_x", "ul": ["U419"]},
             "spec_check": {"thickness_in": 0.04, "board_type": 0.06}, "fetched_at": now()},
            {"query_hash": f"sample-{job_id}-board-bad", "condition_id": cid, "component": "board", "sample": True,
             "url": "https://example.com/sample-1-2-regular.pdf", "title": 'Sample: 1/2" regular gypsum board data sheet',
             "attrs": {"thickness_in": 0.5, "board_type": "regular"},
             "spec_check": {"thickness_in": 0.94, "board_type": 0.91}, "fetched_at": now()},
        ])
        prices = {p["item"]: p["unit_cost"] for p in db.price_book.find()}
        lines = [  # board padded past takeoff + waste so the guard trips
            {"item": "stud_3.625_20ga_10ft", "qty": 80, "unit": "ea", "computed_qty": 76, "waste": 0.05},
            {"item": "track_3.625_20ga_10ft", "qty": 21, "unit": "ea", "computed_qty": 20, "waste": 0.05},
            {"item": "board_0.625_type_x_4x10", "qty": 70, "unit": "ea", "computed_qty": 50, "waste": 0.1},
            {"item": "screws_1.25in", "qty": 2, "unit": "box", "computed_qty": 2, "waste": 0.0},
            {"item": "tape_paper_500ft", "qty": 4, "unit": "roll", "computed_qty": 4, "waste": 0.0},
            {"item": "mud_all_purpose", "qty": 6, "unit": "box", "computed_qty": 6, "waste": 0.0},
        ]
        for line in lines:
            line["unit_cost"] = prices.get(line["item"])
        over = [l["item"] for l in lines if l["qty"] > l["computed_qty"] * (1 + l["waste"])]
        insert_or_review(db, "orders", {"job_id": job_id, "condition_id": cid, "lf": 100, "height_ft": 10, "lines": lines,
                                        "total": round(sum(l["qty"] * (l["unit_cost"] or 0) for l in lines), 2),
                                        "guard": {"over": bool(over), "items_over": over}, "supplier_email": None,
                                        "sent_at": None, "sample": True}, "ui.demo_seed")

    plan = sheets.get("A-101")
    if plan:
        room = db.rooms.find_one({"sheet": plan["id"], "number": "101"})
        ewo = {"job_id": job_id, "sheet": plan["id"], "pin": [300.0, 640.0], "room_id": str(room["_id"]) if room else None,
               "description": EWO_TEXT, "submitted_by": "foreman, synthetic", "status": "new", "created_at": now()}
        ewo_id = str(db.ewos.insert_one(ewo).inserted_id)
        if live:  # what lane 2's route() does
            r = jev.choice(f"{EWO_TEXT} Room: 101 OFFICE", TASKS, "Which task handles this extra work order?", job_id=job_id)
            if r["choice"]:
                try:
                    out = run_task(r["choice"], {"ewo": EWO_TEXT, "room": "101 OFFICE", "conditions": [DRAWING_CONDITION | {"type_tag": "A1"}],
                                                 "price_book": list(db.price_book.find({"item": {"$regex": "3.625_20ga|0.625_type_x_4x10|labor"}}, {"_id": 0}))},
                                   "ewo_drafts", job_id=job_id)
                    insert_or_review(db, "ewo_drafts", {**out["result"], "ewo_id": ewo_id, "job_id": job_id, "task": r["choice"],
                                                        "model": out["model"], "escalated": out["escalated"], "routing": r}, "pipeline.ewo")
                    db.ewos.update_one({"_id": oid(ewo_id)}, {"$set": {"status": "drafted"}})
                except TaskFailed as e:
                    to_review(db, "pipeline.ewo", {"job_id": job_id, "description": EWO_TEXT}, e.reason)
            else:
                to_review(db, "pipeline.ewo", {"job_id": job_id, "description": EWO_TEXT}, "Jev's two answers disagreed")
        print(f"  rooms, product data, order and EWO added")
    return job_id


if __name__ == "__main__":
    live = _live() and "--offline" not in sys.argv
    job = seed(get_db(), live)
    print(f"done ({'live models' if live else 'offline values'}): /jobs/{job}")
