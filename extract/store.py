"""Batch writes for lane 1: one round trip per batch, rejects parked like app.db.insert_or_review does."""
from bson import ObjectId
from pymongo.database import Database
from pymongo.errors import BulkWriteError

from app.db import now


def insert_all(db: Database, collection: str, docs: list[dict], source: str) -> list[ObjectId | None]:
    """insert_many(ordered=False). Each doc MongoDB rejects goes to review_queue with the reason; the result
    has the inserted _id, or None for a rejected doc, in the order of `docs`."""
    if not docs:
        return []
    try:
        db[collection].insert_many(docs, ordered=False)
        return [d["_id"] for d in docs]
    except BulkWriteError as e:
        failed = {err["index"]: err for err in e.details.get("writeErrors", [])}
        db.review_queue.insert_many(
            [{"source": source, "collection": collection, "doc": docs[i], "reason": _reason(err), "at": now()}
             for i, err in failed.items()]
        )
        return [None if i in failed else d["_id"] for i, d in enumerate(docs)]


def _reason(err: dict) -> str:
    details = (err.get("errInfo") or {}).get("details")
    msg = err.get("errmsg", "write rejected")
    return f"{msg}: {details}" if details else msg
