"""Takeoff: click-to-measure back end. A measurement is one line, area or count of points a person drew on a
sheet for a condition; its quantity comes from the sheet's scale.

    linear   sum of the segment lengths / pt_per_ft                    -> LF
    area     shoelace area of the polygon / pt_per_ft^2                -> SF
    count    number of points                                          -> EA

Points are PDF points (origin top-left), exactly what the canvas sends. quantities(job_id) is the $group
over measurements that feeds the quantities table and, for linear footage, pipeline.orders.build_from_takeoff.
"""
import math
import re

from app.db import get_db, insert_or_review, is_mock, now, oid
from contracts.validate import validate
from projects.alternates import ensure_base, scope

KINDS = {"linear": ("lf", 2), "area": ("sf", 3), "count": ("ea", 1)}  # kind -> (unit, minimum points)
QUANTITY_PLACES = 6


class ScaleMissing(ValueError):
    """The sheet has no pt_per_ft, so a drawn length means nothing yet."""


def _measure(kind: str, points: list[list[float]], pt_per_ft: float) -> float:
    if kind == "linear":
        return sum(math.dist(a, b) for a, b in zip(points, points[1:])) / pt_per_ft
    if kind == "area":
        twice = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]))
        return abs(twice) / 2 / pt_per_ft**2
    return float(len(points))


def _find(db, collection: str, hex_id: str, what: str) -> dict:
    doc = db[collection].find_one({"_id": oid(hex_id)}) if oid(hex_id) else None
    if doc is None:
        raise LookupError(f"no such {what}: {hex_id!r}")
    return doc


def add_measurement(job_id: str, sheet_id: str, condition_id: str, kind: str, points: list, height_ft: float | None = None,
                    alternate_id: str | None = None) -> str:
    """Measure `points` on a sheet for a condition and store it. Return the measurement _id hex.
    alternate_id None (or the base bid's id) means the base bid, like every measurement made before alternates.
    LookupError: no such sheet or condition. ScaleMissing: the sheet has no scale. ValueError: anything else wrong."""
    db = get_db()
    sheet = _find(db, "sheets", sheet_id, "sheet")
    condition = _find(db, "conditions", condition_id, "condition")
    if sheet.get("job_id") != job_id or condition.get("job_id") not in (None, job_id):
        raise ValueError("the sheet and condition must belong to the same job")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    unit, least = KINDS[kind]
    try:
        pts = [[float(x), float(y)] for x, y in points]
    except (TypeError, ValueError):
        raise ValueError("points must be a list of [x, y] pairs in PDF points") from None
    if len(pts) < least:
        raise ValueError(f"a {kind} measurement needs at least {least} point{'s' * (least > 1)}")
    pt_per_ft = (sheet.get("scale") or {}).get("pt_per_ft")
    if not pt_per_ft or pt_per_ft <= 0:
        raise ScaleMissing(f"Sheet {sheet.get('sheet_no') or sheet_id} has no scale: set or calibrate the sheet scale first.")

    doc = {
        "job_id": job_id,
        "sheet": sheet_id,
        "condition_id": condition_id,
        "kind": kind,
        "points": pts,
        "quantity": round(_measure(kind, pts, pt_per_ft), QUANTITY_PLACES),
        "unit": unit,
        "pt_per_ft": pt_per_ft,
        "height_ft": height_ft,
        "alternate_id": alternate_id or str(ensure_base(job_id, db)["_id"]),
        "label": None,
        "created_at": now(),
    }
    errors = validate("measurements", doc)  # a clear 422 for the canvas, not a review_queue row
    if errors:
        raise ValueError("; ".join(errors))
    new_id = insert_or_review(db, "measurements", doc, "measurement")
    if new_id is None:
        row = db.review_queue.find_one({"source": "measurement", "doc.created_at": doc["created_at"], "doc.sheet": sheet_id})
        raise ValueError(f"MongoDB rejected the measurement: {row['reason'] if row else 'unknown reason'}")
    return str(new_id)


def _sheet_job(db, sheet_id: str) -> str | None:
    """The job a sheet belongs to, for scoping it by alternate. None for an unknown sheet."""
    sheet = db.sheets.find_one({"_id": oid(sheet_id)}, {"job_id": 1}) if oid(sheet_id) else None
    return (sheet or {}).get("job_id")


def list_measurements(sheet_id: str, alternate_id: str | None = None) -> list[dict]:
    """The sheet's measurements in one alternate, oldest first (drawing order).
    None or the base bid's id = the base bid; an unknown sheet has none."""
    db = get_db()
    job_id = _sheet_job(db, sheet_id)
    if job_id is None:
        return []
    return list(db.measurements.find({"sheet": sheet_id, **scope(job_id, alternate_id)}).sort("_id", 1))


def delete_measurement(measurement_id: str) -> bool:
    return bool(oid(measurement_id)) and get_db().measurements.delete_one({"_id": oid(measurement_id)}).deleted_count == 1


def undo_last(sheet_id: str, alternate_id: str | None = None) -> dict | None:
    """Delete the most recent measurement on a sheet in one alternate; return it, or None when it has none.
    None or the base bid's id = the base bid, so undoing never reaches into another alternate's newest."""
    db = get_db()
    job_id = _sheet_job(db, sheet_id)
    if job_id is None:
        return None
    return db.measurements.find_one_and_delete({"sheet": sheet_id, **scope(job_id, alternate_id)}, sort=[("_id", -1)])


def _natural(tag: str | None) -> list:
    """A2 before A10, rows without a type_tag last."""
    return [(0, int(p)) if p.isdigit() else (1, p.lower()) for p in re.findall(r"\d+|\D+", tag or "￿")]


def quantities(job_id: str, alternate_id: str | None = None) -> list[dict]:
    """One row per condition and unit: {condition_id, type_tag, name, color, unit, quantity, measurements},
    sorted by type_tag, for one alternate (None or the base bid's id = the base bid).
    $group over measurements, then $lookup of the condition's name, color and type_tag."""
    db = get_db()
    group = [
        {"$match": {"job_id": job_id, **scope(job_id, alternate_id)}},
        {"$group": {"_id": {"condition_id": "$condition_id", "unit": "$unit"}, "quantity": {"$sum": "$quantity"}, "measurements": {"$sum": 1}}},
    ]
    if is_mock():  # mongomock has neither $convert nor $lookup with let: join the conditions in Python
        rows = list(db.measurements.aggregate(group))
        ids = {oid(r["_id"]["condition_id"]) for r in rows} - {None}
        conds = {str(c["_id"]): c for c in db.conditions.find({"_id": {"$in": list(ids)}})}
        out = [
            {"condition_id": r["_id"]["condition_id"], "type_tag": (conds.get(r["_id"]["condition_id"]) or {}).get("type_tag"),
             "name": (conds.get(r["_id"]["condition_id"]) or {}).get("name"), "color": (conds.get(r["_id"]["condition_id"]) or {}).get("color"),
             "unit": r["_id"]["unit"], "quantity": r["quantity"], "measurements": r["measurements"]}
            for r in rows
        ]
    else:
        out = list(db.measurements.aggregate([
            *group,
            # condition_id is a hex string; conditions._id is an ObjectId
            {"$addFields": {"cond": {"$convert": {"input": "$_id.condition_id", "to": "objectId", "onError": None, "onNull": None}}}},
            {"$lookup": {"from": "conditions", "localField": "cond", "foreignField": "_id", "as": "c"}},
            {"$set": {"c": {"$arrayElemAt": ["$c", 0]}}},
            {"$project": {
                "_id": 0, "condition_id": "$_id.condition_id", "unit": "$_id.unit", "quantity": 1, "measurements": 1,
                "type_tag": "$c.type_tag", "name": "$c.name", "color": "$c.color",
            }},
        ]))
    for r in out:
        r["quantity"] = round(r["quantity"], 3)
        r.update({k: r.get(k) for k in ("type_tag", "name", "color")})
    return sorted(out, key=lambda r: (_natural(r["type_tag"]), r["unit"]))


def linear_takeoff(job_id: str, condition_id: str, alternate_id: str | None = None) -> tuple[float, float | None]:
    """(total LF measured for a condition in one alternate, the LF-weighted mean of the heights typed on those
    measurements or None). None or the base bid's id = the base bid."""
    rows = list(get_db().measurements.aggregate([
        {"$match": {"job_id": job_id, "condition_id": condition_id, "kind": "linear", **scope(job_id, alternate_id)}},
        {"$group": {
            "_id": None,
            "lf": {"$sum": "$quantity"},
            "lf_with_height": {"$sum": {"$cond": [{"$gt": ["$height_ft", 0]}, "$quantity", 0]}},
            "lf_x_height": {"$sum": {"$cond": [{"$gt": ["$height_ft", 0]}, {"$multiply": ["$quantity", "$height_ft"]}, 0]}},
        }},
    ]))
    if not rows:
        return 0.0, None
    r = rows[0]
    return r["lf"], (round(r["lf_x_height"] / r["lf_with_height"], 2) if r["lf_with_height"] else None)
