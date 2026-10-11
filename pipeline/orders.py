"""Orders: orders.math.lines_for -> $lookup price_book -> $merge into orders -> over-purchase guard.
Lane 2 owns this file; the signatures are the contract other lanes call.

build() inserts the unpriced order, then MongoDB prices it: one aggregation unwinds the lines, $lookups
price_book on item, sets unit_cost, regroups the lines with the total, and $merges the result back over the
same order. guard() is an update pipeline, so the comparison runs in the database too.
"""
import importlib
import importlib.util
import logging

from bson import ObjectId

from app.db import get_db, is_mock, now, oid
from contracts.validate import validate

log = logging.getLogger("materially.orders")

TOTAL_PLACES = 2  # dollars and cents


class OrderLocked(RuntimeError):
    """The order was already sent to the supplier; its lines are frozen."""


def _lines_for():
    """orders.math.lines_for once H2 has merged it, else our stand-in with the same signature."""
    try:
        merged = importlib.util.find_spec("orders.math") is not None
    except (ImportError, ValueError):
        merged = False
    if merged:
        return importlib.import_module("orders.math").lines_for
    from pipeline._lines_stub import lines_for

    return lines_for


def _line(raw: dict) -> dict:
    """A priced-later order line: the math's {item, qty, unit, computed_qty, waste} plus unit_cost null."""
    missing = [k for k in ("item", "qty", "unit") if raw.get(k) is None]
    if missing:
        raise ValueError(f"orders.math returned a line without {', '.join(missing)}: {raw!r}")
    line = dict(raw)
    line.setdefault("computed_qty", line["qty"])
    line.setdefault("waste", 0)
    line["unit_cost"] = None
    return line


def _oid(order_id: str) -> ObjectId:
    o = oid(order_id)
    if o is None:
        raise LookupError(f"no such order: {order_id!r}")
    return o


def _pricing_pipeline(order: ObjectId, *, offline: bool = False) -> list[dict]:
    """Everything up to the $merge. mongomock (offline) has no $round, so it rounds the total in Python."""
    return [
        {"$match": {"_id": order}},
        {"$unwind": {"path": "$lines", "includeArrayIndex": "i"}},
        {"$lookup": {"from": "price_book", "localField": "lines.item", "foreignField": "item", "as": "pb"}},
        # $min of an empty array is null: an item the price book doesn't know stays unpriced.
        {"$set": {"lines.unit_cost": {"$min": "$pb.unit_cost"}}},
        {"$sort": {"i": 1}},
        {"$project": {"pb": 0, "i": 0}},
        {
            "$group": {
                "_id": "$_id",
                "doc": {"$first": "$$ROOT"},
                "lines": {"$push": "$lines"},
                "total": {"$sum": {"$multiply": ["$lines.qty", {"$ifNull": ["$lines.unit_cost", 0]}]}},
            }
        },
        {"$set": {"doc.lines": "$lines", "doc.total": "$total" if offline else {"$round": ["$total", TOTAL_PLACES]}}},
        {"$replaceRoot": {"newRoot": "$doc"}},
    ]


def price(order_id: str) -> None:
    """Fill unit_cost on every line from price_book (cheapest supplier) and set total, inside MongoDB."""
    db = get_db()
    order = _oid(order_id)
    if is_mock():  # no $merge or $round in mongomock: same pipeline, replace the order from Python
        priced = list(db.orders.aggregate(_pricing_pipeline(order, offline=True)))
        if priced:
            priced[0]["total"] = round(priced[0]["total"], TOTAL_PLACES)
            db.orders.replace_one({"_id": order}, priced[0])
        return
    merge = {"$merge": {"into": "orders", "on": "_id", "whenMatched": "replace", "whenNotMatched": "fail"}}
    list(db.orders.aggregate([*_pricing_pipeline(order), merge]))


def retotal(order_id: str) -> None:
    """Recompute total = sum(qty x unit_cost) after a line edit."""
    db, order = get_db(), _oid(order_id)
    if is_mock():  # mongomock can't sum inside an update pipeline
        doc = db.orders.find_one({"_id": order}, {"lines": 1})
        if doc:
            total = sum(line["qty"] * (line.get("unit_cost") or 0) for line in doc["lines"])
            db.orders.update_one({"_id": order}, {"$set": {"total": round(total, TOTAL_PLACES)}})
        return
    line_total = {"$multiply": ["$$l.qty", {"$ifNull": ["$$l.unit_cost", 0]}]}
    db.orders.update_one(
        {"_id": order},
        [{"$set": {"total": {"$round": [{"$sum": {"$map": {"input": "$lines", "as": "l", "in": line_total}}}, TOTAL_PLACES]}}}],
    )


def build(job_id: str, condition_id: str, lf: float, height_ft: float) -> str:
    """Lines from orders.math.lines_for(condition, lf, height_ft), priced by an aggregation that $lookups
    price_book and $merges into orders. Return the order _id hex.
    LookupError: no such condition. ValueError: bad input, or the order fails the orders validator."""
    db = get_db()
    cond_id = oid(condition_id)
    condition = db.conditions.find_one({"_id": cond_id}) if cond_id else None
    if condition is None:
        raise LookupError(f"no such condition: {condition_id!r}")
    if condition.get("job_id") not in (None, job_id):
        raise ValueError(f"condition {condition_id} belongs to another job")
    if not (isinstance(lf, (int, float)) and lf > 0 and isinstance(height_ft, (int, float)) and height_ft > 0):
        raise ValueError("lf and height_ft must be positive numbers")

    lines = [_line(raw) for raw in _lines_for()(condition, lf, height_ft)]
    if not lines:
        raise ValueError("orders.math returned no lines for this condition")
    doc = {
        "job_id": job_id,
        "condition_id": condition_id,
        "lf": lf,
        "height_ft": height_ft,
        "lines": lines,
        "total": None,
        "supplier_email": None,
        "sent_at": None,
        "created_at": now(),
    }
    errors = validate("orders", doc)  # mongomock doesn't validate; Atlas would reject the same doc
    if errors:
        raise ValueError("; ".join(errors))
    order_id = str(db.orders.insert_one(doc).inserted_id)
    price(order_id)
    guard(order_id)
    return order_id


def _over_condition() -> dict:
    """qty > computed_qty x (1 + waste), where the allowance is rounded up to a whole purchase unit: math that
    orders computed x (1 + waste) in whole boxes/sheets must not trip its own guard."""
    padded = {"$multiply": [{"$ifNull": ["$$l.computed_qty", "$$l.qty"]}, {"$add": [1, {"$ifNull": ["$$l.waste", 0]}]}]}
    # The 1e-9 keeps floating point honest: 100 x 1.1 is 110.00000000000001, and ceil would make that 111.
    return {"$gt": ["$$l.qty", {"$ceil": {"$subtract": [padded, 1e-9]}}]}


def guard(order_id: str) -> dict:
    """Compare each line's qty to computed_qty x (1 + waste); set and return guard {over, items_over}.
    items_over lists the item keys of the lines over their allowance. LookupError when the order is missing."""
    updated = get_db().orders.find_one_and_update(
        {"_id": _oid(order_id)},
        [
            {
                "$set": {
                    "guard": {
                        "items_over": {
                            "$map": {
                                "input": {"$filter": {"input": "$lines", "as": "l", "cond": _over_condition()}},
                                "as": "l",
                                "in": "$$l.item",
                            }
                        }
                    }
                }
            },
            {"$set": {"guard.over": {"$gt": [{"$size": "$guard.items_over"}, 0]}}},
        ],
        projection={"guard": 1},
        return_document=True,
    )
    if updated is None:
        raise LookupError(f"no such order: {order_id!r}")
    return {"over": updated["guard"]["over"], "items_over": updated["guard"]["items_over"]}


def set_line_qty(order_id: str, index: int, qty: float) -> dict:
    """Set lines[index].qty, refresh the total, re-run the guard (the UI's padded line), return the guard.
    LookupError: no such order. IndexError: no such line. OrderLocked: already sent."""
    db = get_db()
    order = db.orders.find_one({"_id": _oid(order_id)}, {"lines": 1, "sent_at": 1})
    if order is None:
        raise LookupError(f"no such order: {order_id!r}")
    if order.get("sent_at"):
        raise OrderLocked("this order was already sent to the supplier")
    if not 0 <= index < len(order.get("lines") or []):
        raise IndexError(f"order has no line {index}")
    db.orders.update_one({"_id": order["_id"]}, {"$set": {f"lines.{index}.qty": qty}})
    retotal(order_id)
    return guard(order_id)


def unpriced(order: dict) -> list[str]:
    """Items the price book doesn't know (unit_cost still null)."""
    return [line["item"] for line in order.get("lines") or [] if line.get("unit_cost") is None]
