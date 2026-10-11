"""po routes: the purchase order preview and the (outbox) send.

    GET  /api/orders/{id}/po     the PO as HTML
    POST /api/orders/{id}/send   write the PO to the outbox and mark the order sent
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.db import get_db, oid
from pipeline.orders import OrderLocked
from po.render import render
from po.send import send

router = APIRouter(tags=["po"])


@router.get("/api/orders/{order_id}/po", response_class=HTMLResponse)
def order_po(order_id: str):
    try:
        return HTMLResponse(render(order_id))
    except LookupError as e:
        raise HTTPException(404, str(e))


@router.post("/api/orders/{order_id}/send")
def send_po(order_id: str):
    try:
        outbox_id = send(order_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except OrderLocked as e:
        raise HTTPException(409, str(e))
    row = get_db().outbox.find_one({"_id": oid(outbox_id)})
    return {"outbox_id": outbox_id, "order_id": order_id, "to": row["to"], "subject": row["subject"], "sent_at": row["at"].isoformat()}
