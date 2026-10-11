"""Server-rendered pages (lane 3). Pages read MongoDB directly, so they work before other lanes merge;
actions (build order, edit qty, send PO, drop a pin) call the other lanes' JSON APIs from static/api.js."""
import json
from datetime import datetime
from fractions import Fraction
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db import get_db, jsonable, oid
from contracts.schemas import BOARD_TYPES
from extract.scale import COMMON_LABELS

router = APIRouter(tags=["ui"])
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

SPEC_FLAG = 0.7  # spec check: p(mismatch) above this is flagged
BOARD_LABELS = {
    "regular": "Regular", "type_x": "Type X", "type_c": "Type C", "moisture_resistant": "Moisture resistant",
    "mold_resistant": "Mold resistant", "type_x_mold_resistant": "Type X mold resistant",
    "abuse_resistant": "Abuse resistant", "impact_resistant": "Impact resistant", "shaftliner": "Shaftliner",
    "cement_board": "Cement board", "glass_mat_sheathing": "Glass-mat sheathing", "sound_dampening": "Sound dampening",
}
assert set(BOARD_LABELS) == set(BOARD_TYPES)
TASK_LABELS = {"price_extra": "Price extra", "base_scope_check": "Base scope check", "backcharge": "Backcharge"}


def inches(v) -> str:
    """3.625 -> 3-5/8", 0.625 -> 5/8", 6 -> 6"."""
    if v is None:
        return "—"
    whole = int(v)
    frac = Fraction(v - whole).limit_denominator(16)
    if not frac:
        return f'{whole}"'
    return f'{frac.numerator}/{frac.denominator}"' if not whole else f'{whole}-{frac.numerator}/{frac.denominator}"'


def money(v) -> str:
    return "—" if v is None else f"${v:,.2f}"


def clock(v) -> str:
    if not v:
        return ""
    t = datetime.fromisoformat(v) if isinstance(v, str) else v
    return t.astimezone().strftime("%H:%M")


templates.env.filters.update(
    inches=inches, money=money, clock=clock,
    board=lambda b: BOARD_LABELS.get(b, b or "—"), task=lambda t: TASK_LABELS.get(t, t),
)
templates.env.globals.update(SPEC_FLAG=SPEC_FLAG)


def _job(db, job_id: str) -> dict:
    job = db.jobs.find_one({"_id": oid(job_id)})
    if not job:
        raise HTTPException(404, "No such job.")
    return jsonable(job)


def _page(request: Request, name: str, job: dict | None = None, tab: str = "", **ctx):
    return templates.TemplateResponse(request, name, {"job": job, "tab": tab, **ctx})


def _cost(db, job_id: str) -> dict:
    rows = list(db.usage.aggregate([
        {"$match": {"job_id": job_id}},
        {"$group": {"_id": None, "calls": {"$sum": 1}, "cost_usd": {"$sum": "$cost_usd"}}},
    ]))
    return {"calls": rows[0]["calls"], "cost_usd": round(rows[0]["cost_usd"], 5)} if rows else {"calls": 0, "cost_usd": 0.0}


def _sheets_by_id(db, job_id: str) -> dict:
    return {s["id"]: s for s in jsonable(list(db.sheets.find({"job_id": job_id}).sort("page", 1)))}


@router.get("/")
def index(request: Request):
    jobs = jsonable(list(get_db().jobs.find().sort("created_at", -1).limit(50)))
    return _page(request, "index.html", jobs=jobs)


@router.get("/jobs/{job_id}")
def job_page(request: Request, job_id: str):
    job = _job(get_db(), job_id)
    return _page(request, "job.html", job, "sheets", scale_labels=json.dumps(COMMON_LABELS))


@router.get("/jobs/{job_id}/conditions")
def conditions_page(request: Request, job_id: str):
    db = get_db()
    job = _job(db, job_id)
    conds = jsonable(list(db.conditions.find({"job_id": job_id}).sort("type_tag", 1)))
    return _page(request, "conditions.html", job, "conditions", conditions=conds, sheets=_sheets_by_id(db, job_id))


@router.get("/conditions/{condition_id}")
def condition_page(request: Request, condition_id: str):
    db = get_db()
    cond = db.conditions.find_one({"_id": oid(condition_id)})
    if not cond:
        raise HTTPException(404, "No such condition.")
    cond = jsonable(cond)
    job = _job(db, cond["job_id"])
    sheet = db.sheets.find_one({"_id": oid((cond.get("source") or {}).get("sheet"))})
    products = jsonable(list(db.product_data.find({"condition_id": condition_id}).sort("component", 1)))
    orders = jsonable(list(db.orders.find({"condition_id": condition_id}).sort("_id", -1)))
    return _page(request, "condition.html", job, "conditions", c=cond, sheet=jsonable(sheet), products=products, orders=orders)


@router.get("/jobs/{job_id}/orders")
def orders_page(request: Request, job_id: str):
    db = get_db()
    job = _job(db, job_id)
    orders = jsonable(list(db.orders.find({"job_id": job_id}).sort("_id", -1)))
    conds = {c["id"]: c for c in jsonable(list(db.conditions.find({"job_id": job_id})))}
    return _page(request, "orders.html", job, "orders", orders=orders, conditions=conds)


@router.get("/orders/{order_id}")
def order_page(request: Request, order_id: str):
    db = get_db()
    order = db.orders.find_one({"_id": oid(order_id)})
    if not order:
        raise HTTPException(404, "No such order.")
    order = jsonable(order)
    job = _job(db, order["job_id"])
    cond = jsonable(db.conditions.find_one({"_id": oid(order["condition_id"])}))
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


def _drafts(db, job_id: str, draft_id: str | None = None) -> list[dict]:
    ewos = {e["id"]: e for e in jsonable(list(db.ewos.find({"job_id": job_id})))}
    q = {"$or": [{"job_id": job_id}, {"ewo_id": {"$in": list(ewos)}}]}
    if draft_id:
        q = {"_id": oid(draft_id)}
    drafts = jsonable(list(db.ewo_drafts.find(q).sort("_id", -1).limit(100)))
    room_ids = [oid(e.get("room_id")) for e in ewos.values() if e.get("room_id")]
    rooms = {r["id"]: r for r in jsonable(list(db.rooms.find({"_id": {"$in": room_ids}})))}
    for d in drafts:
        e = ewos.get(d.get("ewo_id")) or jsonable(db.ewos.find_one({"_id": oid(d.get("ewo_id"))})) or {}
        d["ewo"], d["room"] = e, rooms.get(e.get("room_id")) or {}
    return drafts


@router.get("/jobs/{job_id}/pm")
def pm_page(request: Request, job_id: str):
    db = get_db()
    job = _job(db, job_id)
    return _page(request, "pm.html", job, "pm", drafts=_drafts(db, job_id))


@router.get("/ui/drafts/{draft_id}/card", response_class=HTMLResponse)
def draft_card(request: Request, draft_id: str):
    db = get_db()
    d = db.ewo_drafts.find_one({"_id": oid(draft_id)})
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
    return _page(request, "review.html", job, "review", items=items)


@router.get("/ui/jobs/{job_id}/drafts")
def draft_ids(job_id: str) -> list[str]:
    """Newest-first draft ids, for the PM queue's polling fallback before the SSE stream exists."""
    return [d["id"] for d in _drafts(get_db(), job_id)]


@router.get("/ui/jobs/{job_id}/cost")
def job_cost(job_id: str) -> dict:
    return _cost(get_db(), job_id)
