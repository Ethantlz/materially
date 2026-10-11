"""Live PM queue: a change stream on ewo_drafts inserts, served as server-sent events.

    GET /events/pm?job_id=...   ->   data: {full draft doc, with `id` next to `_id`}\\n\\n   (default "message" event)

pymongo's watch() blocks, so it runs in a thread and hands events to the async generator through a queue.
The thread polls try_next() on a short max_await_time, so it sees the stop flag within about half a second
and the stream closes cleanly when the client goes away. mongomock has no change streams (and a standalone
mongod has no oplog to watch): there the thread polls the collection for new _ids instead.
"""
import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator, Callable

from bson import ObjectId
from pymongo.errors import OperationFailure, PyMongoError

from app.db import get_db, is_mock, jsonable, oid

log = logging.getLogger("materially.events")

HEARTBEAT_S = 15.0  # a ":" comment keeps proxies and the browser from timing the stream out
AWAIT_MS = 500  # change stream getMore wait: also how fast a stopped watcher thread exits
POLL_S = 0.25  # offline polling interval
READY_TIMEOUT_S = 10.0

_active = 0
_lock = threading.Lock()


def active_watchers() -> int:
    """Watcher threads still running (0 once every client has gone away)."""
    return _active


def _matches(db, doc: dict, job_id: str | None) -> bool:
    if not job_id:
        return True
    if doc.get("job_id") is not None:
        return doc["job_id"] == job_id
    ewo = db.ewos.find_one({"_id": oid(doc.get("ewo_id"))}, {"job_id": 1}) if oid(doc.get("ewo_id")) else None
    return bool(ewo) and ewo.get("job_id") == job_id


def _watch_change_stream(db, emit: Callable[[dict], None], ready: threading.Event, stop: threading.Event) -> None:
    token = None
    while not stop.is_set():
        try:
            with db.ewo_drafts.watch([{"$match": {"operationType": "insert"}}], max_await_time_ms=AWAIT_MS, resume_after=token) as stream:
                ready.set()  # the stream is open server-side: an insert from here on is delivered
                while not stop.is_set():
                    change = stream.try_next()
                    if change is not None:
                        token = stream.resume_token
                        emit(change["fullDocument"])
        except OperationFailure as e:
            if not ready.is_set():
                raise  # watch() itself is unsupported here (standalone server): the caller falls back to polling
            log.warning("change stream failed (%s); reopening", e)
            token = None
            stop.wait(1.0)
        except PyMongoError as e:
            log.warning("change stream dropped (%s); reopening", e)
            stop.wait(1.0)


def _poll_inserts(db, emit: Callable[[dict], None], ready: threading.Event, stop: threading.Event) -> None:
    newest = db.ewo_drafts.find_one({}, {"_id": 1}, sort=[("_id", -1)])
    last: ObjectId | None = newest["_id"] if newest else None
    ready.set()
    while not stop.wait(POLL_S):
        query = {"_id": {"$gt": last}} if last else {}
        for doc in db.ewo_drafts.find(query).sort("_id", 1):
            last = doc["_id"]
            emit(doc)


def watch_inserts(db, job_id: str | None, put: Callable[[object], None], stop: threading.Event, ready: threading.Event) -> None:
    """Blocking. put(sse_message) for every ewo_drafts insert for job_id (all jobs when None), until stop is set."""
    global _active
    with _lock:
        _active += 1

    def emit(doc: dict) -> None:
        try:
            if not _matches(db, doc, job_id):
                return
            payload = jsonable(doc)
            put(f"id: {payload.get('id', '')}\ndata: {json.dumps(payload)}\n\n")
        except Exception:
            log.exception("could not forward draft %s", doc.get("_id"))

    try:
        if not is_mock():
            try:
                _watch_change_stream(db, emit, ready, stop)
                return
            except OperationFailure as e:
                log.warning("no change streams here (%s); polling ewo_drafts instead", e)
        _poll_inserts(db, emit, ready, stop)
    except Exception:
        log.exception("PM stream watcher died")
    finally:
        ready.set()  # never leave a client waiting on a watcher that is gone
        with _lock:
            _active -= 1


async def pm_stream(request, job_id: str | None) -> AsyncIterator[str]:
    """The SSE body: a connected comment, each new draft, a heartbeat comment every HEARTBEAT_S."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    stop, ready = threading.Event(), threading.Event()

    def put(message) -> None:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, message)
        except RuntimeError:  # the loop is already closed: the client left
            stop.set()

    thread = threading.Thread(target=watch_inserts, args=(get_db(), job_id, put, stop, ready), name="pm-sse-watch", daemon=True)
    thread.start()
    try:
        # First bytes only once the watcher is live, so a client that sees them can't miss an insert.
        deadline = loop.time() + READY_TIMEOUT_S
        while not ready.is_set() and loop.time() < deadline:
            await asyncio.sleep(0.02)
        yield ": connected\n\n"
        last_sent = loop.time()
        while not await request.is_disconnected():
            wait = min(1.0, max(0.05, last_sent + HEARTBEAT_S - loop.time()))
            try:
                message = await asyncio.wait_for(queue.get(), wait)
            except asyncio.TimeoutError:
                if loop.time() - last_sent >= HEARTBEAT_S:
                    yield ": keepalive\n\n"
                    last_sent = loop.time()
                continue
            yield message
            last_sent = loop.time()
    finally:
        stop.set()
