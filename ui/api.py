"""Server-rendered pages (lane 3). Pages read MongoDB directly, so they work before other lanes merge;
actions (measure, build an order, edit qty, send PO, drop a pin) call the other lanes' JSON APIs from
static/api.js, which falls back to ui/standins.py while a route isn't merged yet."""
import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db import get_db, jsonable, oid
from contracts.schemas import GAUGES
from extract.scale import COMMON_LABELS
from ui import standins
from ui.common import (  # noqa: F401  (inches and reason_summary are re-exported for the tests)
    BOARD_LABELS, PALETTE, condition_views, discipline, inches, next_color, reason_summary, stud_text,
)

router = APIRouter(tags=["ui"])
router.include_router(standins.router)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

SPEC_FLAG = 0.7  # spec check: p(mismatch) above this is flagged
TASK_LABELS = {"price_extra": "Price extra", "base_scope_check": "Base scope check", "backcharge": "Backcharge"}
KIND_LABELS = {
    "partition_types": "Partition types", "floor_plan": "Floor plan", "reflected_ceiling": "RCP", "elevations": "Elevations",
    "details": "Details", "schedules": "Schedules", "other": "Other",
}


def money(v) -> str:
    return "—" if v is None else f"${v:,.2f}"


def clock(v) -> str:
    if not v:
        return ""
    t = datetime.fromisoformat(v) if isinstance(v, str) else v
    return t.astimezone().strftime("%H:%M")


templates.env.filters.update(
    inches=inches, money=money, clock=clock, studs=stud_text,
    board=lambda b: BOARD_LABELS.get(b, b or "—"), task=lambda t: TASK_LABELS.get(t, t),
    kind=lambda k: KIND_LABELS.get(k, k or "—"),
)
templates.env.globals.update(SPEC_FLAG=SPEC_FLAG)


def _job(db, job_id: str) -> dict:
    job = db.jobs.find_one({"_id": oid(job_id)}) if oid(job_id) else None
    if not job:
        raise HTTPException(404, "No such job.")
    return jsonable(job)


def _page(request: Request, name: str, job: dict | None = None, tab: str = "", **ctx):
    return templates.TemplateResponse(request, name, {"job": job, "tab": tab, **ctx})


def _cost(db, job_id: str) -> dict:
    rows = list(db.usage.aggregate([
        {"$match": {"job_id": job_id}},
        {"$group": {"_id": {"task": "$task", "model": "$model"}, "calls": {"$sum": 1}, "cost_usd": {"$sum": "$cost_usd"}}},
    ]))
    by_task = sorted(
        ({"task": r["_id"]["task"], "model": r["_id"]["model"], "calls": r["calls"], "cost_usd": round(r["cost_usd"], 6)} for r in rows),
        key=lambda r: (-r["cost_usd"], -r["calls"]),
    )
    return {"calls": sum(r["calls"] for r in by_task), "cost_usd": round(sum(r["cost_usd"] for r in by_task), 5), "by_task": by_task}


def _sheet_view(s: dict) -> dict:
    return {
        "id": s["id"], "page": s.get("page"), "sheet_no": s.get("sheet_no"), "title": s.get("title"), "kind": s.get("kind"),
        "scale": s.get("scale"), "image_path": s.get("image_path"), "thumb_path": s.get("thumb_path"),
        "image_scale": s.get("image_scale"), "size_pt": s.get("size_pt"), "discipline": discipline(s.get("sheet_no")),
    }


def _sheets(db, job_id: str) -> list[dict]:
    return [_sheet_view(s) for s in jsonable(list(db.sheets.find({"job_id": job_id}).sort("page", 1)))]


def _sheets_by_id(db, job_id: str) -> dict:
    return {s["id"]: s for s in jsonable(list(db.sheets.find({"job_id": job_id}).sort("page", 1)))}


def _conditions(db, job_id: str) -> list[dict]:
    return condition_views(jsonable(list(db.conditions.find({"job_id": job_id}))))


def _measured(db, job_id: str) -> dict[str, dict]:
    """condition id -> {lf, sf, ea}: what has been measured on the drawings so far."""
    out: dict[str, dict] = {}
    for m in db.measurements.find({"job_id": job_id}):
        row = out.setdefault(m["condition_id"], {"lf": 0.0, "sf": 0.0, "ea": 0.0})
        row[m["unit"]] += m["quantity"]
    return out


@router.get("/")
def index(request: Request):
    jobs = jsonable(list(get_db().jobs.find().sort("created_at", -1).limit(50)))
    return _page(request, "index.html", jobs=jobs)


@router.get("/jobs/{job_id}")
def job_page(request: Request, job_id: str):
    job = _job(get_db(), job_id)
    return _page(request, "job.html", job, "sheets", scale_labels=json.dumps(COMMON_LABELS))


@router.get("/jobs/{job_id}/takeoff")
def takeoff_page(request: Request, job_id: str, sheet: str | None = None, cond: str | None = None, focus: str | None = None):
    """The takeoff workspace: sheet rail, drawing canvas, conditions panel, quantities drawer."""
    db = get_db()
    job = _job(db, job_id)
    sheets, conds = _sheets(db, job_id), _conditions(db, job_id)
    by_id = {s["id"]: s for s in sheets}
    focused = next((c for c in conds if c["id"] == focus), None)
    fsrc = (focused or {}).get("source") or {}
    ready = [s for s in sheets if s["image_path"]]
    default = next((s for s in ready if s["kind"] == "floor_plan"), None) or (ready[0] if ready else None) or (sheets[0] if sheets else None)
    current = by_id.get(sheet) or by_id.get(fsrc.get("sheet")) or default
    boot = {
        "job": {"id": job["id"], "name": job["name"], "status": job.get("status")},
        "sheets": sheets, "conditions": conds, "sheet": current["id"] if current else None,
        "cond": cond if any(c["id"] == cond for c in conds) else None,
        "focus": {"condition": focused["id"], "tag": focused["type_tag"], "sheet": fsrc.get("sheet"), "bbox": fsrc.get("bbox")}
        if focused and fsrc.get("bbox") else None,
        "scaleLabels": COMMON_LABELS, "boards": BOARD_LABELS, "gauges": GAUGES, "palette": PALETTE,
        "nextColor": next_color([c["color"] for c in conds]),
    }
    return _page(request, "takeoff.html", job, "takeoff", boot=boot)


@router.get("/ui/jobs/{job_id}/data")
def job_data(job_id: str) -> dict:
    """Fresh sheets and conditions for the takeoff workspace (polled while the job is still processing)."""
    db = get_db()
    job = _job(db, job_id)
    return {"job": {"id": job["id"], "name": job["name"], "status": job.get("status")},
            "sheets": _sheets(db, job_id), "conditions": _conditions(db, job_id)}


@router.get("/ui/sheets/{sheet_id}")
def sheet_data(sheet_id: str) -> dict:
    sheet = get_db().sheets.find_one({"_id": oid(sheet_id)}) if oid(sheet_id) else None
    if not sheet:
        raise HTTPException(404, "No such sheet.")
    return _sheet_view(jsonable(sheet))


@router.get("/jobs/{job_id}/conditions")
def conditions_page(request: Request, job_id: str):
    db = get_db()
    job = _job(db, job_id)
    conds = jsonable(list(db.conditions.find({"job_id": job_id}).sort("type_tag", 1)))
    views = {v["id"]: v for v in condition_views(conds)}
    rejected = []
    # a validator rejection carries collection == "conditions"; a parse that failed before the write carries only the source
    parse_items = db.review_queue.find({"doc.job_id": job_id, "$or": [{"collection": "conditions"}, {"source": {"$regex": "partition|parse"}}]})
    for it in jsonable(list(parse_items.sort("at", -1).limit(20))):
        rejected.append({"tag": (it.get("doc") or {}).get("type_tag") or "?", "why": reason_summary(it.get("reason", "")), "at": it.get("at")})
    return _page(request, "conditions.html", job, "conditions", conditions=conds, views=views, sheets=_sheets_by_id(db, job_id),
                 rejected=rejected, measured=_measured(db, job_id))


@router.get("/conditions/{condition_id}")
def condition_page(request: Request, condition_id: str):
    db = get_db()
    cond = db.conditions.find_one({"_id": oid(condition_id)}) if oid(condition_id) else None
    if not cond:
        raise HTTPException(404, "No such condition.")
    cond = jsonable(cond)
    job = _job(db, cond["job_id"])
    sheet = db.sheets.find_one({"_id": oid((cond.get("source") or {}).get("sheet"))})
    products = jsonable(list(db.product_data.find({"condition_id": condition_id}).sort("component", 1)))
    orders = jsonable(list(db.orders.find({"condition_id": condition_id}).sort("_id", -1)))
    view = next((v for v in _conditions(db, cond["job_id"]) if v["id"] == condition_id), None)
    return _page(request, "condition.html", job, "conditions", c=cond, v=view, sheet=jsonable(sheet), products=products,
                 orders=orders, measured=_measured(db, cond["job_id"]).get(condition_id))


@router.get("/jobs/{job_id}/orders")
def orders_page(request: Request, job_id: str):
    db = get_db()
    job = _job(db, job_id)
    orders = jsonable(list(db.orders.find({"job_id": job_id}).sort("_id", -1)))
    conds = {c["id"]: c for c in _conditions(db, job_id)}
    return _page(request, "orders.html", job, "orders", orders=orders, conditions=conds)


@router.get("/orders/{order_id}")
def order_page(request: Request, order_id: str):
    db = get_db()
    order = db.orders.find_one({"_id": oid(order_id)}) if oid(order_id) else None
    if not order:
        raise HTTPException(404, "No such order.")
    order = jsonable(order)
    job = _job(db, order["job_id"])
    cond = next((c for c in _conditions(db, order["job_id"]) if c["id"] == order["condition_id"]), None)
    # items_over may hold item names or whole lines, depending on lane 2.
    over = [x.get("item") if isinstance(x, dict) else x for x in (order.get("guard") or {}).get("items_over") or []]
    return _page(request, "order.html", job, "orders", o=order, c=cond, over=over)


@router.get("/jobs/{job_id}/ewo")
def ewo_page(request: Request, job_id: str, sheet: str | None = None):
    db = get_db()
    job = _job(db, job_id)
    sheets = [s for s in _sheets_by_id(db, job_id).values() if s.get("image_path")]
    sheets.sort(key=lambda s: (s.get("kind") != "floor_plan", s.get("page", 0)))
    current = next((s for s in sheets if s["id"] == sheet), sheets[0] if sheets else None)
    rooms = jsonable(list(db.rooms.find({"job_id": job_id, "sheet": current["id"]}))) if current else []
    return _page(request, "ewo.html", job, "ewo", sheets=sheets, sheet=current, rooms=rooms)


def pin_crop(sheet: dict | None, pin, w: int = 360, h: int = 150, zoom: float = 0.5) -> str:
    """CSS for a w x h window of the sheet image centred on a pin (zoom = css px per image px)."""
    if not sheet or not sheet.get("image_path") or not sheet.get("size_pt") or not pin:
        return ""
    ips = sheet.get("image_scale") or 1.5
    cx, cy = pin[0] * ips * zoom, pin[1] * ips * zoom
    return (f"background-image:url({sheet['image_path']});background-size:{sheet['size_pt'][0] * ips * zoom:.0f}px "
            f"{sheet['size_pt'][1] * ips * zoom:.0f}px;background-position:{w / 2 - cx:.0f}px {h / 2 - cy:.0f}px")


def _drafts(db, job_id: str, draft_id: str | None = None) -> list[dict]:
    ewos = {e["id"]: e for e in jsonable(list(db.ewos.find({"job_id": job_id})))}
    q = {"$or": [{"job_id": job_id}, {"ewo_id": {"$in": list(ewos)}}]}
    if draft_id:
        q = {"_id": oid(draft_id)}
    drafts = jsonable(list(db.ewo_drafts.find(q).sort("_id", -1).limit(100)))
    room_ids = [oid(e.get("room_id")) for e in ewos.values() if e.get("room_id")]
    rooms = {r["id"]: r for r in jsonable(list(db.rooms.find({"_id": {"$in": room_ids}})))}
    sheets = {}
    for d in drafts:
        e = ewos.get(d.get("ewo_id")) or jsonable(db.ewos.find_one({"_id": oid(d.get("ewo_id"))})) or {}
        d["ewo"], d["room"] = e, rooms.get(e.get("room_id")) or {}
        sid = e.get("sheet")
        if sid and sid not in sheets:
            sheets[sid] = jsonable(db.sheets.find_one({"_id": oid(sid)})) if oid(sid) else None
        d["sheet"] = sheets.get(sid) or {}
        d["crop"] = pin_crop(d["sheet"], e.get("pin"))
    return drafts


@router.get("/jobs/{job_id}/pm")
def pm_page(request: Request, job_id: str):
    db = get_db()
    job = _job(db, job_id)
    return _page(request, "pm.html", job, "pm", drafts=_drafts(db, job_id))


@router.get("/ui/drafts/{draft_id}/card", response_class=HTMLResponse)
def draft_card(request: Request, draft_id: str):
    db = get_db()
    d = db.ewo_drafts.find_one({"_id": oid(draft_id)}) if oid(draft_id) else None
    if not d:
        raise HTTPException(404, "No such draft.")
    ewo = db.ewos.find_one({"_id": oid(d.get("ewo_id"))}) or {}
    drafts = _drafts(db, d.get("job_id") or ewo.get("job_id") or "", draft_id)
    return templates.TemplateResponse(request, "_draft.html", {"d": drafts[0]})


@router.get("/review")
def review_page(request: Request, job_id: str | None = None):
    db = get_db()
    job = _job(db, job_id) if job_id else None
    q = {"$or": [{"doc.job_id": job_id}, {"job_id": job_id}]} if job_id else {}
    items = jsonable(list(db.review_queue.find(q).sort("at", -1).limit(200)))
    for it in items:
        doc = it.get("doc")
        it["doc_json"] = json.dumps(doc, indent=1, default=str)[:4000]
        label = (doc.get("type_tag") or str(doc.get("description") or "")[:40]) if isinstance(doc, dict) else str(doc or "")[:40]
        it["doc_label"] = label or "show"
        it["reason_short"] = reason_summary(it.get("reason", ""))
    return _page(request, "review.html", job, "review", items=items)


@router.get("/ui/jobs/{job_id}/drafts")
def draft_ids(job_id: str) -> list[str]:
    """Newest-first draft ids, for the PM queue's polling fallback before the SSE stream exists."""
    return [d["id"] for d in _drafts(get_db(), job_id)]


@router.get("/ui/jobs/{job_id}/cost")
def job_cost(job_id: str) -> dict:
    return _cost(get_db(), job_id)
