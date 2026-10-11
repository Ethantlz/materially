"""MongoDB access. `MONGODB_URI=mongomock://` gives an in-memory DB for tests and offline UI work."""
import logging
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import WriteError

from app.settings import get_settings

log = logging.getLogger("materially.db")

def is_mock(uri: str | None = None) -> bool:
    return (uri if uri is not None else get_settings().mongodb_uri).startswith("mongomock://")


@lru_cache(maxsize=4)
def get_client(uri: str) -> MongoClient:
    if is_mock(uri):
        import mongomock

        return mongomock.MongoClient(tz_aware=True)
    if not uri:
        raise RuntimeError(
            "MONGODB_URI is not set. Start with `infisical run --env=dev -- <cmd>` or put it in ~/.c15/keys.env."
        )
    return MongoClient(uri, tz_aware=True, appname="materially", serverSelectionTimeoutMS=8000)


def get_db(name: str | None = None) -> Database:
    s = get_settings()
    return get_client(s.mongodb_uri)[name or s.db_name]


def now() -> datetime:
    return datetime.now(timezone.utc)


def oid(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def jsonable(value: Any) -> Any:
    """ObjectId -> str, datetime -> ISO string, recursively; `_id` is also exposed as `id`."""
    if isinstance(value, dict):
        out = {k: jsonable(v) for k, v in value.items()}
        if "_id" in out:
            out["id"] = out["_id"]
        return out
    if isinstance(value, list):
        return [jsonable(v) for v in value]
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def insert_or_review(db: Database, collection: str, doc: dict, source: str) -> ObjectId | None:
    """Insert into a validated collection. If MongoDB rejects the doc, park it in review_queue with the reason."""
    try:
        return db[collection].insert_one(doc).inserted_id
    except WriteError as e:
        db.review_queue.insert_one(
            {"source": source, "collection": collection, "doc": doc, "reason": _reason(e), "at": now()}
        )
        return None


def to_review(db: Database, source: str, doc: Any, reason: str) -> None:
    """Park something that failed routing or a model's validation (not a write) for a human."""
    db.review_queue.insert_one({"source": source, "doc": doc, "reason": reason[:2000], "at": now()})


def log_usage(
    db: Database,
    *,
    task: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
    ms: float = 0,
    job_id: str | None = None,
    ok: bool = True,
    error: str | None = None,
) -> None:
    """One usage row per model call, failed calls included. Never raises: logging must not break a call."""
    row = {
        "job_id": str(job_id) if job_id is not None else None,
        "task": task,
        "model": model,
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "cost_usd": float(cost_usd or 0.0),
        "ms": round(float(ms), 1),
        "at": now(),
        "ok": ok,
    }
    if error:
        row["error"] = error[:500]
    try:
        db.usage.insert_one(row)
    except Exception:
        log.exception("usage row not written for %s/%s", task, model)


def _reason(e: WriteError) -> str:
    details = (e.details or {}).get("errInfo", {}).get("details")
    return f"{e.details.get('errmsg', str(e))}: {details}" if details else str(e)
