"""PO: HTML render and the outbox send (email is off)."""
import pytest
from bson import ObjectId

import po
from app.db import now
from pipeline import orders
from po.send import DEFAULT_TO
from tests.pipeline.conftest import add_condition, by_id, new_id


@pytest.fixture
def order_id(db, job):
    db.jobs.insert_one({"_id": ObjectId(job), "name": "Sample <Clinic> Fit-Out", "created_at": now(), "status": "ready"})
    return orders.build(job, add_condition(db, job), 100, 10)


def test_render_shows_job_supplier_lines_and_total(db, job, order_id):
    html = po.render(order_id)
    order = by_id(db.orders, order_id)

    assert "Sample &lt;Clinic&gt; Fit-Out" in html and "<Clinic>" not in html  # escaped
    assert "Sample Supply Co" in html and f"PO-{order_id[-6:].upper()}" in html and "A1, 100 LF x 10 ft" in html
    for l in order["lines"]:
        assert l["item"] in html
    stud = next(l for l in order["lines"] if l["item"] == "stud_3.625_20ga_10ft")
    assert f"${stud['qty'] * stud['unit_cost']:,.2f}" in html and "$10.15" in html  # unit cost and extension
    assert f"${order['total']:,.2f}" in html


def test_render_marks_unpriced_lines(db, job):
    cid = add_condition(db, job, studs={"size_in": 3.625, "gauge": 22, "spacing_in": 16})
    html = po.render(orders.build(job, cid, 100, 10))
    assert html.count("unpriced") == 4  # stud and track: unit cost and extension each


def test_send_writes_the_outbox_and_sets_sent_at(db, job, order_id):
    before = by_id(db.orders, order_id)
    assert before["sent_at"] is None

    outbox_id = po.send(order_id)

    row = by_id(db.outbox, outbox_id)
    assert (row["order_id"], row["job_id"], row["to"]) == (order_id, job, DEFAULT_TO)
    assert row["subject"] == f"Purchase order PO-{order_id[-6:].upper()} - Sample <Clinic> Fit-Out"
    assert row["html"] == po.render(order_id) and row["at"]
    after = by_id(db.orders, order_id)
    assert after["sent_at"] is not None and after["supplier_email"] == DEFAULT_TO
    assert after["lines"] == before["lines"] and after["total"] == before["total"]  # sending changes nothing else


def test_send_uses_the_orders_supplier_email_and_only_once(db, job, order_id):
    db.orders.update_one({"_id": ObjectId(order_id)}, {"$set": {"supplier_email": "desk@supplier.example"}})

    row = by_id(db.outbox, po.send(order_id))

    assert row["to"] == "desk@supplier.example"
    with pytest.raises(orders.OrderLocked):
        po.send(order_id)
    assert db.outbox.count_documents({"order_id": order_id}) == 1


def test_unknown_order(db):
    for call in (po.send, po.render):
        with pytest.raises(LookupError):
            call(new_id())


def test_po_routes(db, job, order_id, client):
    r = client.get(f"/api/orders/{order_id}/po")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "Sample Supply Co" in r.text and "stud_3.625_20ga_10ft" in r.text

    sent = client.post(f"/api/orders/{order_id}/send")
    assert sent.status_code == 200, sent.text
    body = sent.json()
    assert (body["order_id"], body["to"]) == (order_id, DEFAULT_TO) and body["sent_at"] and body["subject"].startswith("Purchase order PO-")
    assert db.outbox.count_documents({"order_id": order_id}) == 1
    assert client.get(f"/api/orders/{order_id}").json()["sent_at"]  # the order page's "PO sent" state

    assert client.post(f"/api/orders/{order_id}/send").status_code == 409
    assert client.patch(f"/api/orders/{order_id}/lines/0", json={"qty": 1}).status_code == 409  # lines freeze once sent
    assert client.get(f"/api/orders/{new_id()}/po").status_code == 404
    assert client.post(f"/api/orders/{new_id()}/send").status_code == 404


def test_the_po_prints_assumed_next_to_lines_sized_on_an_assumption(db, job):
    plain = po.render(orders.build(job, add_condition(db, job), 100, 10))
    assert "(assumed)" not in plain

    sa4 = add_condition(db, job, type_tag="SA4", studs={"size_in": 3.625, "gauge": None, "spacing_in": 16})
    html = po.render(orders.build(job, sa4, 100, 10))

    assert html.count('class="assumed"') == 2  # studs and track
    assert "stud_3.625_20ga_10ft <span" in html and "(assumed)" in html
    assert "gauge 20 (not on drawings)" in html and "unpriced" not in html
