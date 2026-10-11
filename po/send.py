"""Send a purchase order. Email is off: the PO goes to an `outbox` collection and the order is marked sent.

    outbox_id = send(order_id)
    outbox row: {order_id, job_id, to, subject, html, at};  orders.sent_at is set (and supplier_email to `to`).
LookupError: no such order. pipeline.orders.OrderLocked: the order was already sent.
"""
from app.db import get_db, now, oid
from pipeline.orders import OrderLocked
from po.render import job_name, po_number, render

DEFAULT_TO = "orders@sample-supply.example"


def send(order_id: str) -> str:
    db = get_db()
    order = db.orders.find_one({"_id": oid(order_id)}) if oid(order_id) else None
    if order is None:
        raise LookupError(f"no such order: {order_id!r}")
    to = order.get("supplier_email") or DEFAULT_TO
    at = now()
    # Claim the order first: two clicks on Send must not write two POs.
    claimed = db.orders.update_one({"_id": order["_id"], "sent_at": None}, {"$set": {"sent_at": at, "supplier_email": to}})
    if claimed.matched_count == 0:
        raise OrderLocked("this order was already sent to the supplier")
    row = {
        "order_id": order_id,
        "job_id": order["job_id"],
        "to": to,
        "subject": f"Purchase order {po_number(order)} - {job_name(db, order)}",
        "html": render(order_id),
        "at": at,
    }
    return str(db.outbox.insert_one(row).inserted_id)
