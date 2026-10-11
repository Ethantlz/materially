"""EWOs: a foreman's pin on a sheet -> room ($near on rooms) -> Jev picks the task -> run_task drafts it.
Lane 2 owns this file; the signatures are the contract other lanes call.

    ewo_id = create(job_id, sheet, x, y, description)    # pin -> nearest room label on that sheet
    draft_id = route(ewo_id)                              # Jev picks the task, DeepSeek drafts, validator checks

A draft that passes lands in ewo_drafts (and the PM queue's change stream); anything unclear or rejected
lands in review_queue with the reason, and the EWO's status becomes "review".
"""
import logging
import math

from app import jev
from app.db import get_db, insert_or_review, is_mock, now, oid, to_review
from app.router import TaskFailed, run_task
from contracts.validate import validate

log = logging.getLogger("materially.ewo")

NEAR_PT = 400  # a pin farther than this from every room label on its sheet belongs to no room
SUBMITTED_BY = "foreman, synthetic"
QUESTION = "Which task handles this extra work order?"
TASKS = {  # the model_policy tasks Jev may pick, with the one-line descriptions it sees
    "price_extra": "Price new work the owner or GC added that is not in the base contract (an extra).",
    "base_scope_check": "Check whether the described work is already in the base contract scope, so no extra is due.",
    "backcharge": "Damage or rework caused by another trade, to be charged back to that trade.",
}
MAX_CONDITIONS = 20
STUD_LENGTHS = (8, 9, 10, 12, 14, 16)
BOARD_LENGTHS = (8, 10, 12)
GENERIC_ITEMS = [  # priced items almost every extra uses
    "screws_1.25in", "screws_1.625in", "mud_all_purpose", "mud_setting_45", "tape_paper_500ft", "corner_bead_10ft",
    "insulation_batt_2.5in", "insulation_batt_3.5in", "insulation_batt_6in",
    "labor_framing_hr", "labor_hanging_hr", "labor_finishing_hr",
]
DEFAULT_ITEMS = [  # when the job has no conditions yet
    *(f"stud_3.625_20ga_{n}ft" for n in (8, 10, 12)), "track_3.625_20ga_10ft",
    *(f"board_{t}_{b}_4x{n}" for t, b in ((0.5, "regular"), (0.625, "type_x")) for n in BOARD_LENGTHS),
]


def _nearest_room(db, job_id: str, sheet: str, x: float, y: float) -> dict | None:
    """The room whose label_pt is nearest the pin on the same sheet, within NEAR_PT points."""
    if not is_mock():
        return db.rooms.find_one({"label_pt": {"$near": [x, y], "$maxDistance": NEAR_PT}, "sheet": sheet, "job_id": job_id})
    # mongomock has no $near: same answer by scanning the sheet's rooms
    rooms = [r for r in db.rooms.find({"sheet": sheet, "job_id": job_id}) if r.get("label_pt")]
    best = min(rooms, key=lambda r: math.dist(r["label_pt"], (x, y)), default=None)
    return best if best is not None and math.dist(best["label_pt"], (x, y)) <= NEAR_PT else None


def create(job_id: str, sheet: str, x: float, y: float, description: str) -> str:
    """Insert into ewos with room_id from $near on rooms of the same sheet; return the ewo _id hex.
    ValueError when the EWO fails the ewos contract (pin outside the sheet, empty description)."""
    db = get_db()
    doc = {
        "job_id": job_id,
        "sheet": sheet,
        "pin": [x, y],
        "room_id": None,
        "description": description,
        "submitted_by": SUBMITTED_BY,
        "status": "new",
        "created_at": now(),
    }
    errors = validate("ewos", doc)  # before $near: the 2d index rejects a point outside 0-5000 with an opaque error
    if errors:
        raise ValueError("; ".join(errors))
    room = _nearest_room(db, job_id, sheet, x, y)
    doc["room_id"] = str(room["_id"]) if room else None
    return str(db.ewos.insert_one(doc).inserted_id)


def _set_status(db, ewo: dict, status: str) -> None:
    db.ewos.update_one({"_id": ewo["_id"]}, {"$set": {"status": status}})
    ewo["status"] = status


def _park(db, ewo: dict, source: str, reason: str, **extra) -> None:
    """Send the EWO to review_queue (the doc carries job_id and description so the review page shows it)."""
    doc = {"job_id": ewo["job_id"], "ewo_id": str(ewo["_id"]), "description": ewo["description"], **extra}
    to_review(db, source, doc, reason)
    _set_status(db, ewo, "review")


def fail(ewo_id: str, reason: str) -> None:
    """Park an EWO whose routing crashed (not a model rejection) in review_queue so it isn't stuck on "new"."""
    db = get_db()
    ewo = db.ewos.find_one({"_id": oid(ewo_id)}) if oid(ewo_id) else None
    if ewo is not None:
        _park(db, ewo, "ewo_route", reason)


def _price_items(conditions: list[dict]) -> list[str]:
    """price_book keys an extra on these walls may use (key format: contracts/seed.py)."""
    items = list(GENERIC_ITEMS)
    for c in conditions:
        studs = c.get("studs") or {}
        if studs.get("size_in") is not None and studs.get("gauge") is not None:
            size, gauge = studs["size_in"], studs["gauge"]
            items += [f"stud_{size:g}_{gauge}ga_{n}ft" for n in STUD_LENGTHS] + [f"track_{size:g}_{gauge}ga_10ft"]
        for layer in c.get("layers_each_side") or []:
            items += [f"board_{layer['thickness_in']:g}_{layer['board_type']}_4x{n}" for n in BOARD_LENGTHS]
    return sorted(set(items) if conditions else set(items) | set(DEFAULT_ITEMS))


def _context(db, ewo: dict) -> dict:
    """What run_task sees: the EWO, its room, the job's wall conditions and the price rows they can use."""
    room = db.rooms.find_one({"_id": oid(ewo["room_id"])}) if ewo.get("room_id") else None
    keep = ("type_tag", "studs", "layers_each_side", "sides", "height_ft", "fire_rating")
    conditions = [
        {k: c[k] for k in keep if c.get(k) is not None}
        for c in db.conditions.find({"job_id": ewo["job_id"]}).sort("type_tag", 1).limit(MAX_CONDITIONS)
    ]
    prices = db.price_book.find({"item": {"$in": _price_items(conditions)}}, {"_id": 0, "item": 1, "unit": 1, "unit_cost": 1})
    return {
        "ewo": {"description": ewo["description"], "submitted_by": ewo.get("submitted_by"), "pin": ewo.get("pin")},
        "room": {"name": room.get("name"), "number": room.get("number")} if room else None,
        "conditions": conditions,
        "price_book": sorted(prices, key=lambda p: p["item"]),
    }


def _choose_task(db, ewo: dict, context: dict) -> tuple[str | None, dict | None, str]:
    """(task, routing, why-not). model_policy is read on every call, so disabling a task in Compass stops
    Jev picking it. One enabled task needs no vote; none or a disagreement goes to review."""
    enabled = {p["task"] for p in db.model_policy.find({"task": {"$in": list(TASKS)}, "enabled": True}, {"task": 1})}
    options = {task: text for task, text in TASKS.items() if task in enabled}
    if not options:
        return None, None, "no EWO task is enabled in model_policy (" + ", ".join(TASKS) + ")"
    if len(options) == 1:
        task = next(iter(options))
        routing = {"choice": task, "agreed": True, "probabilities": {task: 1.0}, "model": "rules:single-option", "ms": 0.0,
                   "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}, "answers": []}
        return task, routing, ""
    state = {"extra_work_order": ewo["description"], "room": context["room"]}
    try:
        routing = jev.choice(state, options, QUESTION, task="ewo_route", job_id=ewo["job_id"])
    except jev.JevError as e:
        return None, None, f"Jev unavailable: {e}"
    if not routing.get("agreed") or routing.get("choice") not in options:
        return None, routing, "Jev's two option orderings disagreed on the task; a person should pick it"
    return routing["choice"], routing, ""


def _settle_total(draft: dict) -> None:
    """total = sum(qty x unit_cost): arithmetic is code's job, not the model's. Keep the model's figure if it differed."""
    try:
        total = round(sum(li["qty"] * li["unit_cost"] for li in draft["line_items"]), 2)
    except (KeyError, TypeError):
        return  # the validator will say what is wrong
    if abs(total - (draft.get("total") or 0)) > 0.01:
        draft["total_model"], draft["total"] = draft.get("total"), total


def route(ewo_id: str) -> str | None:
    """app.jev.choice over the enabled model_policy tasks (price_extra, base_scope_check, backcharge).
    Not agreed -> review_queue. Agreed -> run_task(task, {...}, "ewo_drafts") -> insert into ewo_drafts.
    Return the draft _id hex, or None when it went to review_queue. LookupError when the EWO doesn't exist."""
    db = get_db()
    ewo = db.ewos.find_one({"_id": oid(ewo_id)}) if oid(ewo_id) else None
    if ewo is None:
        raise LookupError(f"no such ewo: {ewo_id!r}")
    context = _context(db, ewo)

    task, routing, why = _choose_task(db, ewo, context)
    if task is None:
        _park(db, ewo, "ewo_route", why, routing=routing)
        return None
    _set_status(db, ewo, "routed")

    try:
        out = run_task(task, context, "ewo_drafts", job_id=ewo["job_id"])
    except TaskFailed as e:
        _park(db, ewo, task, e.reason, task=task, routing=routing, raw=(e.raw or "")[:2000])
        return None

    draft = {
        **out["result"],
        "ewo_id": str(ewo["_id"]),
        "job_id": ewo["job_id"],
        "task": task,
        "model": out["model"],
        "escalated": out["escalated"],
        "routing": routing,
    }
    _settle_total(draft)
    draft_id = insert_or_review(db, "ewo_drafts", draft, task)  # a validator rejection parks the draft in review_queue
    if draft_id is None:
        _set_status(db, ewo, "review")
        return None
    _set_status(db, ewo, "drafted")
    return str(draft_id)
