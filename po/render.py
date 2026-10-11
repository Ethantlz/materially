"""Purchase order HTML for an order: job name, supplier, the lines with extensions, and the total.

    html = render(order_id)      # LookupError when there is no such order
"""
from html import escape

from app.db import get_db, oid
from contracts.seed import SUPPLIER

STYLE = """
body{font:14px/1.45 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#1b1f24;margin:0;padding:32px;background:#fff}
h1{font-size:22px;margin:0 0 4px}.muted{color:#5b6570}
.meta{display:flex;gap:48px;margin:18px 0 24px}.meta div{min-width:160px}
.meta b{display:block;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#5b6570;font-weight:600}
table{border-collapse:collapse;width:100%}th,td{padding:7px 10px;border-bottom:1px solid #e3e6ea;text-align:left}
th{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#5b6570}.num{text-align:right;font-variant-numeric:tabular-nums}
tfoot td{border-bottom:0;font-weight:700;font-size:15px}.mono{font-family:ui-monospace,Consolas,monospace;font-size:13px}
"""


def money(value) -> str:
    return "unpriced" if value is None else f"${value:,.2f}"


def number(value) -> str:
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def po_number(order: dict) -> str:
    return f"PO-{str(order['_id'])[-6:].upper()}"


def job_name(db, order: dict) -> str:
    job = db.jobs.find_one({"_id": oid(order["job_id"])}) if oid(order["job_id"]) else None
    return (job or {}).get("name") or order["job_id"]


def render(order_id: str) -> str:
    db = get_db()
    order = db.orders.find_one({"_id": oid(order_id)}) if oid(order_id) else None
    if order is None:
        raise LookupError(f"no such order: {order_id!r}")
    rows = "".join(
        f'<tr><td class="mono">{escape(str(l["item"]))}</td><td class="num">{number(l["qty"])}</td><td>{escape(str(l.get("unit") or ""))}</td>'
        f'<td class="num">{money(l.get("unit_cost"))}</td><td class="num">{money(None if l.get("unit_cost") is None else l["qty"] * l["unit_cost"])}</td></tr>'
        for l in order["lines"]
    )
    cond = db.conditions.find_one({"_id": oid(order["condition_id"])}) if oid(order.get("condition_id")) else None
    scope = f'{escape(cond["type_tag"])}, ' if cond and cond.get("type_tag") else ""
    created = order.get("created_at")
    height = f" x {number(order['height_ft'])} ft" if order.get("height_ft") else ""
    name = escape(job_name(db, order))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{po_number(order)} - {name}</title><style>{STYLE}</style></head>
<body data-testid="po-preview">
<h1>Purchase order {po_number(order)}</h1>
<div class="muted">{name}</div>
<div class="meta">
  <div><b>Supplier</b>{escape(SUPPLIER)}</div>
  <div><b>Project</b>{name}</div>
  <div><b>Scope</b>{scope}{number(order["lf"])} LF{height}</div>
  <div><b>Date</b>{f"{created:%Y-%m-%d}" if created else ""}</div>
</div>
<table>
<thead><tr><th>Item</th><th class="num">Qty</th><th>Unit</th><th class="num">Unit cost</th><th class="num">Extension</th></tr></thead>
<tbody>{rows}</tbody>
<tfoot><tr><td colspan="4" class="num">Total</td><td class="num">{money(order.get("total"))}</td></tr></tfoot>
</table>
</body></html>
"""
