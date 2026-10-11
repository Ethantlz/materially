"""GET /events/pm: a real uvicorn server (an endless SSE body can't be read through TestClient), a real client."""
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from app.db import now
from pipeline import events
from pipeline.api import router
from tests.pipeline.conftest import new_id


@pytest.fixture
def server(db):
    """The pipeline router on a free local port, in a thread; yields its base URL."""
    app = FastAPI()
    app.include_router(router)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    assert srv.started, "uvicorn did not start"
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=10)


def draft(job_id, explanation):
    return {
        "ewo_id": new_id(), "job_id": job_id, "task": "price_extra", "model": "deepseek-ai/DeepSeek-V4.1-Flash",
        "line_items": [{"desc": "studs", "qty": 5, "unit": "ea", "unit_cost": 10.15}], "total": 50.75,
        "explanation": explanation, "routing": {"choice": "price_extra"}, "escalated": False, "at": now(),
    }


def wait_for(predicate, seconds=5.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_stream_emits_within_10s_of_an_insert(db, job, server):
    other_job = new_id()
    with httpx.stream("GET", f"{server}/events/pm", params={"job_id": job}, timeout=15) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        lines = r.iter_lines()
        assert next(lines) == ": connected"  # the watcher is live: nothing inserted from here on is missed

        t0 = time.time()
        db.ewo_drafts.insert_one(draft(other_job, "another job's draft: filtered out"))
        inserted = db.ewo_drafts.insert_one(draft(job, "this job's draft"))
        data = next(line for line in lines if line.startswith("data:"))
        elapsed = time.time() - t0

    db.ewo_drafts.delete_many({"job_id": {"$in": [job, other_job]}})
    doc = json.loads(data.removeprefix("data:").strip())
    assert elapsed < 10
    assert doc["explanation"] == "this job's draft"  # the other job's insert, which came first, was skipped
    assert doc["id"] == doc["_id"] == str(inserted.inserted_id)  # the full document, with `id` beside `_id`
    assert doc["line_items"][0]["qty"] == 5 and doc["job_id"] == job and doc["routing"] == {"choice": "price_extra"}


def test_stream_without_a_job_filter_carries_every_job(db, job, server):
    with httpx.stream("GET", f"{server}/events/pm", timeout=15) as r:
        lines = r.iter_lines()
        assert next(lines) == ": connected"
        db.ewo_drafts.insert_one(draft(job, "unfiltered"))
        data = next(line for line in lines if line.startswith("data:"))
    db.ewo_drafts.delete_many({"job_id": job})
    assert json.loads(data.removeprefix("data:"))["explanation"] == "unfiltered"


def test_heartbeat_and_clean_close_on_disconnect(db, server, monkeypatch):
    monkeypatch.setattr(events, "HEARTBEAT_S", 0.3)
    before = events.active_watchers()

    with httpx.stream("GET", f"{server}/events/pm", timeout=15) as r:
        lines = r.iter_lines()
        assert next(lines) == ": connected"
        assert wait_for(lambda: events.active_watchers() == before + 1)
        assert next(line for line in lines if line) == ": keepalive"  # nothing inserted, still talking

    assert wait_for(lambda: events.active_watchers() == before), "the watcher thread outlived its client"


def test_change_stream_is_used_on_a_real_server(live_db):
    """Not the polling fallback: the live run must be fed by watch()."""
    assert not events.is_mock()
    with live_db.ewo_drafts.watch([{"$match": {"operationType": "insert"}}], max_await_time_ms=500) as stream:
        doc = draft(new_id(), "change stream")
        inserted = live_db.ewo_drafts.insert_one(doc).inserted_id
        try:
            change = None
            for _ in range(20):
                change = stream.try_next()
                if change:
                    break
            assert change and change["fullDocument"]["_id"] == inserted
        finally:
            live_db.ewo_drafts.delete_one({"_id": inserted})
