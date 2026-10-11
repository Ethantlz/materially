"""procure lane routes. The integrator includes this router in app/main.py; add endpoints here."""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_db, jsonable, oid
from procure import find, sources

log = logging.getLogger("materially.procure")
router = APIRouter(tags=["procure"])

PULL_AT_ONCE = 4  # materials searched at the same time: Firecrawl concurrency on small plans
NO_KEY = "Product search needs a Firecrawl key (FIRECRAWL_API_KEY in Infisical)."  # as find.Unavailable says it
ALREADY_PULLING = "Already pulling product data for this condition."
_pulling: dict[str, threading.Lock] = {}  # one lock per condition: a second click mid-pull must not re-spend Firecrawl credits
_pulling_guard = threading.Lock()


class SourceIn(BaseModel):
    url: str = ""
    name: str | None = None


@router.get("/api/product-sources")
def list_product_sources():
    return sources.list_sources()


@router.post("/api/product-sources")
def add_product_source(body: SourceIn):
    try:
        return sources.add_source(body.url, body.name)
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.delete("/api/product-sources/{source_id}")
def delete_product_source(source_id: str):
    try:
        sources.delete_source(source_id)
    except LookupError as e:
        raise HTTPException(404, str(e))
    return {"deleted": source_id}


@router.post("/api/conditions/{cid}/materials/{key}/products")
def material_products(cid: str, key: str):
    try:
        return find.find_for_material(cid, key)
    except find.SearchFailed as e:
        raise HTTPException(502, str(e))
    except find.Unavailable as e:
        raise HTTPException(409, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e))


def _pull_one(cid: str, key: str, stop: threading.Event) -> tuple[str, str | dict | None]:
    """One material's pull, run in the pool. Never raises: returns (outcome, value) with outcome ok (the product), error
    (a sentence for errors[key]), blocked or unavailable (a sentence that ends the whole run), or skipped (the run
    had already ended before this one started)."""
    if stop.is_set():
        return "skipped", None
    try:
        return "ok", find.find_for_material(cid, key)
    except find.Unavailable as e:
        stop.set()
        return "unavailable", str(e)
    except find.SearchBlocked as e:  # the key, the credits or the rate limit: the next material would fail the same way
        stop.set()  # set here, not only by the caller, so a worker about to take the next material sees it
        return "blocked", str(e)
    except (find.SearchFailed, LookupError) as e:
        return "error", str(e)
    except Exception:  # one material's surprise must not cost the others their paid-for searches
        log.exception("procure: unexpected failure pulling material %r of condition %s", key, cid)
        return "error", "Couldn't search for this material."


@router.post("/api/conditions/{cid}/procure")
def procure_all(cid: str):
    cond = get_db().conditions.find_one({"_id": oid(cid)}) if oid(cid) else None
    if cond is None:
        raise HTTPException(404, "No such condition.")
    if not find._key():  # once, before any work
        raise HTTPException(409, NO_KEY)
    with _pulling_guard:
        lock = _pulling.setdefault(str(cond["_id"]), threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(409, ALREADY_PULLING)
    try:
        return _pull_all(cid, cond.get("materials") or [])
    finally:
        lock.release()


def _pull_all(cid: str, materials: list[dict]) -> dict:
    stop, results, ended = threading.Event(), {}, None
    with ThreadPoolExecutor(max_workers=max(1, min(PULL_AT_ONCE, len(materials)))) as pool:
        futures = {pool.submit(_pull_one, cid, m["key"], stop): i for i, m in enumerate(materials)}
        for f in as_completed(futures):
            if f.cancelled():
                continue
            outcome, value = results[futures[f]] = f.result()
            if outcome in ("blocked", "unavailable") and ended is None:
                ended = (outcome, value)
                for g in futures:  # everything that hasn't started; the ones running finish (leaving the block waits for them)
                    g.cancel()
    if ended:
        raise HTTPException(409 if ended[0] == "unavailable" else 502, ended[1])
    out, errors = [], {}
    for i, m in enumerate(materials):  # the materials' own order, not the order they finished in
        outcome, value = results[i]
        if outcome == "ok":
            out.append(value)
        else:
            errors[m["key"]] = value
    return {"products": out, "errors": errors}


@router.get("/api/conditions/{cid}/products")
def condition_products(cid: str):
    cond = get_db().conditions.find_one({"_id": oid(cid)}) if oid(cid) else None
    ids = [oid(m["product_id"]) for m in (cond or {}).get("materials") or [] if m.get("product_id")]
    return jsonable(list(get_db().product_data.find({"_id": {"$in": ids}})))
