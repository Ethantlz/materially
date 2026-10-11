"""Stand-ins for takeoff endpoints that other lanes are still building.

The takeoff workspace talks to the real routes first (static/api.js: POST /api/measurements, GET
/api/jobs/{id}/quantities, POST /api/sheets/{id}/calibrate, POST /api/jobs/{id}/conditions ...). While one of
those answers "Not Found" the workspace falls back to the route here, so the screen works end to end on the
`measurements` contract. Both write the same collections, so nothing diverges when the real lane merges.

Delete this file (and its include in ui/api.py) once lane/pipeline and lane/extract are merged; static/api.js
then never reaches the fallbacks.
"""
from math import hypot
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import get_db, insert_or_review, jsonable, now, oid
from ui.common import condition_views, reason_summary

router = APIRouter(prefix="/ui/standin", tags=["ui-standins"])

UNITS = {"linear": "lf", "area": "sf", "count": "ea"}
MIN_POINTS = {"linear": 2, "area": 3, "count": 1}
COORD_MAX = 5000.0  # contracts/schemas.py POINT bounds
CONDITION_FIELDS = ("type_tag", "name", "color", "studs", "layers_each_side", "sides", "insulation", "fire_rating",
                    "ul_assembly", "stc", "height_ft")


def quantity(kind: str, points: list[list[float]], pt_per_ft: float | None) -> float:
    """linear: sum of segment lengths / pt_per_ft (LF); area: shoelace / pt_per_ft^2 (SF); count: points (EA)."""
    if kind == "count":
        return float(len(points))
    if not pt_per_ft:
        raise ValueError("Set or calibrate the sheet scale first.")
    if kind == "linear":
        return sum(hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:])) / pt_per_ft
    twice = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(points, points[1:] + points[:1]))
    return abs(twice) / 2 / pt_per_ft**2


class MeasurementIn(BaseModel):
    job_id: str
    sheet: str
    condition_id: str
    kind: Literal["linear", "area", "count"]
    points: list[list[float]] = Field(min_length=1)
    height_ft: float | None = None
    label: str | None = None


class CalibrateIn(BaseModel):
    p1: list[float] = Field(min_length=2, max_length=2)
    p2: list[float] = Field(min_length=2, max_length=2)
    real_ft: float = Field(gt=0)


def _sheet(db, sheet_id: str) -> dict:
    sheet = db.sheets.find_one({"_id": oid(sheet_id)}) if oid(sheet_id) else None
    if not sheet:
        raise HTTPException(404, "No such sheet.")
    return sheet


@router.get("/sheets/{sheet_id}/measurements")
def sheet_measurements(sheet_id: str):
    return jsonable(list(get_db().measurements.find({"sheet": sheet_id}).sort("_id", 1)))


@router.post("/measurements")
def add_measurement(body: MeasurementIn):
    db = get_db()
    sheet = _sheet(db, body.sheet)
    if not oid(body.condition_id) or not db.conditions.find_one({"_id": oid(body.condition_id)}, {"_id": 1}):
        raise HTTPException(404, "No such condition.")
    if len(body.points) < MIN_POINTS[body.kind]:
        raise HTTPException(422, f"A {body.kind} measurement needs at least {MIN_POINTS[body.kind]} points.")
    points = [[round(min(max(p[0], 0.0), COORD_MAX), 2), round(min(max(p[1], 0.0), COORD_MAX), 2)] for p in body.points]
    pt_per_ft = (sheet.get("scale") or {}).get("pt_per_ft")
    try:
        qty = quantity(body.kind, points, pt_per_ft)
    except ValueError as e:
        raise HTTPException(422, str(e))
    doc = {
        "job_id": body.job_id, "sheet": body.sheet, "condition_id": body.condition_id, "kind": body.kind,
        "points": points, "quantity": round(qty, 3), "unit": UNITS[body.kind], "pt_per_ft": pt_per_ft,
        "height_ft": body.height_ft, "label": body.label, "created_at": now(),
    }
    mid = insert_or_review(db, "measurements", doc, "ui.standin")
    if mid is None:
        raise HTTPException(422, "MongoDB's validator rejected that measurement; it is in the review queue.")
    return {"id": str(mid), "measurement": jsonable({**doc, "_id": mid})}


@router.delete("/measurements/{measurement_id}")
def delete_measurement(measurement_id: str):
    res = get_db().measurements.delete_one({"_id": oid(measurement_id)})
    if not res.deleted_count:
        raise HTTPException(404, "No such measurement.")
    return {"deleted": measurement_id}


@router.post("/sheets/{sheet_id}/measurements/undo")
def undo_measurement(sheet_id: str):
    db = get_db()
    last = db.measurements.find_one({"sheet": sheet_id}, sort=[("_id", -1)])
    if not last:
        raise HTTPException(404, "Nothing to undo on this sheet.")
    db.measurements.delete_one({"_id": last["_id"]})
    return {"deleted": str(last["_id"])}


@router.get("/jobs/{job_id}/quantities")
def quantities(job_id: str):
    """One row per condition and unit, sorted by type tag: the shape of lane 2's $group over measurements."""
    db = get_db()
    conds = {c["id"]: c for c in condition_views(jsonable(list(db.conditions.find({"job_id": job_id}))))}
    totals: dict[tuple[str, str], dict] = {}
    for m in db.measurements.find({"job_id": job_id}):
        row = totals.setdefault((m["condition_id"], m["unit"]), {"quantity": 0.0, "count": 0})
        row["quantity"] += m["quantity"]
        row["count"] += 1
    rows = []
    for (cid, unit), t in totals.items():
        c = conds.get(cid) or {}
        rows.append({"condition_id": cid, "unit": unit, "quantity": round(t["quantity"], 3), "count": t["count"],
                     "type_tag": c.get("type_tag"), "name": c.get("name"), "color": c.get("color"), "height_ft": c.get("height_ft")})
    rows.sort(key=lambda r: (r["type_tag"] or "~", r["unit"]))
    return rows


@router.post("/sheets/{sheet_id}/calibrate")
def calibrate(sheet_id: str, body: CalibrateIn):
    db = get_db()
    _sheet(db, sheet_id)
    dist = hypot(body.p2[0] - body.p1[0], body.p2[1] - body.p1[1])
    if dist < 1:
        raise HTTPException(422, "Pick two points that are further apart.")
    pt_per_ft = round(dist / body.real_ft, 4)
    ratio = round(864 / pt_per_ft, 2)  # real length / paper length (96 for 1/8" = 1'-0")
    scale = {"label": f"calibrated 1:{ratio:g}", "pt_per_ft": pt_per_ft, "ratio": ratio, "source": "manual",
             "confidence": 1.0, "candidates": [f"calibrated 1:{ratio:g}"]}
    db.sheets.update_one({"_id": oid(sheet_id)}, {"$set": {"scale": scale}})
    return scale


@router.post("/jobs/{job_id}/conditions")
def create_condition(job_id: str, body: dict):
    db = get_db()
    if not oid(job_id) or not db.jobs.find_one({"_id": oid(job_id)}, {"_id": 1}):
        raise HTTPException(404, "No such job.")
    if not str(body.get("type_tag") or "").strip():
        raise HTTPException(422, "A condition needs a type tag.")
    doc = {k: body[k] for k in CONDITION_FIELDS if body.get(k) not in (None, "")}
    doc["type_tag"] = str(doc["type_tag"]).strip()
    doc.update(job_id=job_id, routing={"model": "manual", "escalated": False})
    cid = insert_or_review(db, "conditions", doc, "ui.standin")
    if cid is None:
        item = db.review_queue.find_one({"source": "ui.standin", "collection": "conditions"}, sort=[("at", -1)])
        raise HTTPException(422, reason_summary((item or {}).get("reason", "MongoDB's validator rejected that condition.")))
    return {"id": str(cid), "condition_id": str(cid)}
