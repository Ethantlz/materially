"""Project alternates. Drawings and the conditions library belong to the project; measurements, quantities and
orders belong to one alternate. Every project has a Base bid, made on first use. A measurement or order with no
alternate_id counts as the base bid (everything made before alternates existed)."""
from pymongo.errors import DuplicateKeyError

from app.db import get_db, jsonable, now, oid

BASE_NAME = "Base bid"


def ensure_base(job_id: str, db=None) -> dict:
    db = db if db is not None else get_db()
    base = db.alternates.find_one({"job_id": job_id, "kind": "base"})
    if base:
        return base
    doc = {"job_id": job_id, "name": BASE_NAME, "kind": "base", "description": None, "order": 0, "created_at": now()}
    try:
        doc["_id"] = db.alternates.insert_one(doc).inserted_id
    except DuplicateKeyError:  # two first requests at once: the other one made it
        return db.alternates.find_one({"job_id": job_id, "kind": "base"})
    return doc


def list_alternates(job_id: str) -> list[dict]:
    db = get_db()
    ensure_base(job_id, db)
    return jsonable(list(db.alternates.find({"job_id": job_id}).sort([("order", 1), ("created_at", 1)])))


def _clean(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise ValueError("An alternate needs a name.")
    return name[:80]


def create_alternate(job_id: str, name: str, description: str | None = None) -> str:
    db = get_db()
    ensure_base(job_id, db)
    doc = {"job_id": job_id, "name": _clean(name), "kind": "alternate", "description": description,
           "order": db.alternates.count_documents({"job_id": job_id}), "created_at": now()}
    try:
        return str(db.alternates.insert_one(doc).inserted_id)
    except DuplicateKeyError:
        raise ValueError(f"This project already has an alternate called {doc['name']!r}.") from None


def _get(alt_id: str) -> dict:
    a = get_db().alternates.find_one({"_id": oid(alt_id)}) if oid(alt_id) else None
    if a is None:
        raise LookupError("No such alternate.")
    return a


def rename_alternate(alt_id: str, name: str) -> None:
    a = _get(alt_id)
    try:
        get_db().alternates.update_one({"_id": a["_id"]}, {"$set": {"name": _clean(name)}})
    except DuplicateKeyError:
        raise ValueError("Another alternate already has that name.") from None


class InUse(RuntimeError):
    pass


def delete_alternate(alt_id: str) -> None:
    db, a = get_db(), _get(alt_id)
    if a["kind"] == "base":
        raise InUse("The base bid can't be deleted.")
    f = {"alternate_id": alt_id}
    if db.measurements.count_documents(f, limit=1) or db.orders.count_documents(f, limit=1):
        raise InUse("This alternate has measurements or orders. Delete them first.")
    db.alternates.delete_one({"_id": a["_id"]})


def scope(job_id: str, alternate_id: str | None) -> dict:
    """Filter fragment selecting one alternate's measurements or orders. None or the base id = base bid,
    which also owns the docs that predate alternates (no alternate_id)."""
    base = str(ensure_base(job_id)["_id"])
    if not alternate_id or alternate_id == base:
        return {"alternate_id": {"$in": [base, None]}}
    return {"alternate_id": alternate_id}


def project_rows() -> list[dict]:
    """Every project, newest first, with its alternates: what the projects page lists."""
    db = get_db()
    jobs = jsonable(list(db.jobs.find().sort("created_at", -1).limit(200)))
    for j in jobs:
        j["alternates"] = list_alternates(j["id"])
    return jobs
