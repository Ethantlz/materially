"""pipeline lane routes: field EWOs, drafts, the live PM stream, priced orders with the guard, cost per job.

    POST /api/ewos                         pin + description -> ewo (room by $near); routing runs in the background
    GET  /api/ewos?job_id                  the job's EWOs, newest first
    POST /api/ewos/{id}/route              (re)route one EWO now
    GET  /api/drafts?job_id                the job's drafts, newest first
    GET  /events/pm?job_id                 SSE: each new ewo_drafts insert (change stream)
    POST /api/measurements                 points drawn on a sheet for a condition -> LF / SF / EA from the sheet scale
    GET  /api/sheets/{id}/measurements     the sheet's measurements, oldest first
    DELETE /api/measurements/{id}          remove one
    POST /api/sheets/{id}/measurements/undo  remove the sheet's latest
    GET  /api/jobs/{job_id}/quantities     measurements $grouped by condition and unit, sorted by type_tag
    POST /api/jobs/{job_id}/conditions/{cid}/order  priced order from the condition's measured LF
    POST /api/orders                       condition x LF x height -> priced order
    GET  /api/orders/{id}                  the order and its unpriced items
    PATCH /api/orders/{id}/lines/{i}       change a line's qty -> total and guard re-run
    POST /api/orders/{id}/guard            re-run the over-purchase guard
    GET  /api/jobs/{job_id}/cost           usage $group: calls, cost_usd, by task and model
"""
import logging
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.db import get_db, jsonable, oid
from pipeline import events
from pipeline import ewo as ewo_mod
from pipeline import orders as orders_mod
from pipeline import takeoff

log = logging.getLogger("materially.pipeline")

router = APIRouter(tags=["pipeline"])

LIST_LIMIT = 200


# --- EWOs and drafts --------------------------------------------------------------------------------------------

class EwoIn(BaseModel):
    job_id: str
    sheet: str  # sheets._id hex
    x: float  # PDF points, origin top-left
    y: float
    description: str = Field(min_length=1)
    room_id: str | None = None  # the foreman's dropdown choice; wins over $near when it is a room on this sheet


def _route_in_background(ewo_id: str) -> None:
    try:
        ewo_mod.route(ewo_id)
    except Exception as e:  # a crash (not a model rejection) must not leave the EWO stuck on "new"
        log.exception("routing ewo %s failed", ewo_id)
        ewo_mod.fail(ewo_id, f"routing crashed: {type(e).__name__}: {e}")


@router.post("/api/ewos")
def create_ewo(body: EwoIn, background: BackgroundTasks):
    db = get_db()
    try:
        ewo_id = ewo_mod.create(body.job_id, body.sheet, body.x, body.y, body.description.strip())
    except ValueError as e:
        raise HTTPException(422, str(e))
    if body.room_id and oid(body.room_id):
        chosen = db.rooms.find_one({"_id": oid(body.room_id), "job_id": body.job_id, "sheet": body.sheet}, {"_id": 1})
        if chosen:
            db.ewos.update_one({"_id": oid(ewo_id)}, {"$set": {"room_id": body.room_id}})
    background.add_task(_route_in_background, ewo_id)
    ewo = db.ewos.find_one({"_id": oid(ewo_id)})
    room = db.rooms.find_one({"_id": oid(ewo["room_id"])}) if ewo.get("room_id") else None
    return {"ewo_id": ewo_id, "id": ewo_id, "room_id": ewo.get("room_id"), "room": jsonable(room), "status": ewo["status"]}


@router.get("/api/ewos")
def list_ewos(job_id: str):
    return jsonable(list(get_db().ewos.find({"job_id": job_id}).sort("_id", -1).limit(LIST_LIMIT)))


@router.post("/api/ewos/{ewo_id}/route")
def route_ewo(ewo_id: str):
    try:
        draft_id = ewo_mod.route(ewo_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    ewo = get_db().ewos.find_one({"_id": oid(ewo_id)})
    return {"ewo_id": ewo_id, "draft_id": draft_id, "status": ewo["status"]}


@router.get("/api/drafts")
def list_drafts(job_id: str):
    db = get_db()
    ewos = {str(e["_id"]): e for e in db.ewos.find({"job_id": job_id})}
    query = {"$or": [{"job_id": job_id}, {"ewo_id": {"$in": list(ewos)}}]}
    drafts = list(db.ewo_drafts.find(query).sort("_id", -1).limit(LIST_LIMIT))
    room_ids = [oid(e["room_id"]) for e in ewos.values() if e.get("room_id")]
    rooms = {str(r["_id"]): r for r in db.rooms.find({"_id": {"$in": room_ids}})}
    for d in drafts:
        ewo = ewos.get(d.get("ewo_id")) or {}
        d["ewo"] = {k: ewo.get(k) for k in ("description", "submitted_by", "status", "pin", "room_id", "created_at")} if ewo else None
        room = rooms.get(ewo.get("room_id")) or {}
        d["room"] = {"name": room.get("name"), "number": room.get("number")} if room else None
    return jsonable(drafts)


@router.get("/events/pm")
async def pm_events(request: Request, job_id: str | None = None):
    return StreamingResponse(
        events.pm_stream(request, job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# --- takeoff ----------------------------------------------------------------------------------------------------

class MeasurementIn(BaseModel):
    job_id: str
    sheet: str | None = None  # sheets._id hex; `sheet_id` is accepted too
    sheet_id: str | None = None
    condition_id: str
    kind: Literal["linear", "area", "count"]
    points: list[list[float]]  # [[x, y], ...] in PDF points, origin top-left
    height_ft: float | None = Field(default=None, ge=0, le=60)
    alternate_id: str | None = None  # None = the base bid


@router.post("/api/measurements")
def add_measurement(body: MeasurementIn):
    sheet = body.sheet or body.sheet_id
    if not sheet:
        raise HTTPException(422, "sheet is required")
    try:
        new_id = takeoff.add_measurement(body.job_id, sheet, body.condition_id, body.kind, body.points, body.height_ft, body.alternate_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:  # includes takeoff.ScaleMissing
        raise HTTPException(422, str(e))
    return jsonable(get_db().measurements.find_one({"_id": oid(new_id)}))


@router.get("/api/sheets/{sheet_id}/measurements")
def list_measurements(sheet_id: str):
    return jsonable(takeoff.list_measurements(sheet_id))


@router.delete("/api/measurements/{measurement_id}")
def delete_measurement(measurement_id: str):
    if not takeoff.delete_measurement(measurement_id):
        raise HTTPException(404, "No such measurement.")
    return {"deleted": measurement_id}


@router.post("/api/sheets/{sheet_id}/measurements/undo")
def undo_measurement(sheet_id: str):
    gone = takeoff.undo_last(sheet_id)
    if gone is None:
        raise HTTPException(404, "Nothing to undo on this sheet.")
    return {"deleted": str(gone["_id"]), "measurement": jsonable(gone)}


@router.get("/api/jobs/{job_id}/quantities")
def job_quantities(job_id: str, alternate_id: str | None = None):
    return takeoff.quantities(job_id, alternate_id)


@router.post("/api/jobs/{job_id}/conditions/{condition_id}/order")
def order_from_takeoff(job_id: str, condition_id: str, alternate_id: str | None = None):
    try:
        order_id = orders_mod.build_from_takeoff(job_id, condition_id, alternate_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return _build_response(order_id)


# --- orders -----------------------------------------------------------------------------------------------------

class OrderIn(BaseModel):
    job_id: str
    condition_id: str
    lf: float = Field(gt=0)
    height_ft: float = Field(gt=0)


class QtyIn(BaseModel):
    qty: float = Field(ge=0)


def _order_view(order: dict) -> dict:
    return {**jsonable(order), "unpriced": orders_mod.unpriced(order)}


def _get_order(order_id: str) -> dict:
    order = get_db().orders.find_one({"_id": oid(order_id)}) if oid(order_id) else None
    if order is None:
        raise HTTPException(404, "No such order.")
    return order


def _build_response(order_id: str) -> dict:
    order = _order_view(_get_order(order_id))
    return {"order_id": order_id, "id": order_id, "total": order["total"], "unpriced": order["unpriced"], "guard": order["guard"], "order": order}


@router.post("/api/orders")
def build_order(body: OrderIn):
    try:
        order_id = orders_mod.build(body.job_id, body.condition_id, body.lf, body.height_ft)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return _build_response(order_id)


@router.get("/api/orders/{order_id}")
def get_order(order_id: str):
    return _order_view(_get_order(order_id))


@router.patch("/api/orders/{order_id}/lines/{index}")
def patch_line(order_id: str, index: int, body: QtyIn):
    try:
        orders_mod.set_line_qty(order_id, index, body.qty)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except IndexError as e:
        raise HTTPException(404, str(e))
    except orders_mod.OrderLocked as e:
        raise HTTPException(409, str(e))
    return _order_view(_get_order(order_id))


@router.post("/api/orders/{order_id}/guard")
def run_guard(order_id: str):
    try:
        return orders_mod.guard(order_id)
    except LookupError as e:
        raise HTTPException(404, str(e))


# --- cost -------------------------------------------------------------------------------------------------------

@router.get("/api/jobs/{job_id}/cost")
def job_cost(job_id: str):
    """Every model call is a usage row; $group them into the "N decisions on this job: $0.0X" line."""
    rows = list(
        get_db().usage.aggregate(
            [
                {"$match": {"job_id": job_id}},
                {"$group": {"_id": {"task": "$task", "model": "$model"}, "calls": {"$sum": 1}, "cost_usd": {"$sum": "$cost_usd"}}},
                {"$sort": {"cost_usd": -1, "calls": -1}},
                {
                    "$group": {
                        "_id": None,
                        "calls": {"$sum": "$calls"},
                        "cost_usd": {"$sum": "$cost_usd"},
                        "by_task": {"$push": {"task": "$_id.task", "model": "$_id.model", "calls": "$calls", "cost_usd": "$cost_usd"}},
                    }
                },
            ]
        )
    )
    if not rows:
        return {"calls": 0, "cost_usd": 0.0, "by_task": []}
    row = rows[0]
    return {
        "calls": row["calls"],
        "cost_usd": round(row["cost_usd"], 6),
        "by_task": [{**t, "cost_usd": round(t["cost_usd"], 6)} for t in row["by_task"]],
    }
