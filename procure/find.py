"""Product data for a condition's materials: one supplier search per material, cached by query hash so a
repeat search makes no Firecrawl call; attributes read by run_task; each specified attribute checked by Jev."""
import hashlib

import httpx
from pymongo.errors import DuplicateKeyError

from app import jev
from app.db import get_db, jsonable, now, oid
from app.router import TaskFailed, run_task
from app.settings import get_settings

FIRECRAWL = "https://api.firecrawl.dev/v1/search"
CHECKED = ("thickness_in", "board_type", "gauge", "size_in")  # material attributes a product must match


class Unavailable(RuntimeError):
    pass


class SearchFailed(RuntimeError):
    """Firecrawl or Jev failed on a search we did start: the caller answers 502 with this sentence,
    never a bare plain-text 500 the UI can't show."""


def _key() -> str:
    return get_settings().firecrawl_api_key


def query_for(material: dict) -> str:
    return f"{material['desc'].split(',')[0]} product data sheet"


def _search(query: str) -> list[dict]:
    try:
        r = httpx.post(FIRECRAWL, headers={"Authorization": f"Bearer {_key()}"},
                       json={"query": query, "limit": 3, "scrapeOptions": {"formats": ["markdown"]}}, timeout=60)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise SearchFailed(f"Firecrawl said {e.response.status_code}") from None
    except httpx.HTTPError:
        raise SearchFailed("Couldn't reach Firecrawl") from None
    return r.json().get("data") or []


def _attrs(text: str, job_id: str) -> dict:
    return run_task("extract_product_attrs", text[:12000], "product_attrs", job_id=job_id)["result"]


def _p_mismatch(material: dict, attrs: dict, attr: str, job_id: str) -> float:
    q = f"Does this product fail the specified {attr}: {material.get(attr)}?"
    try:
        return jev.noul({"specified": material, "product": attrs}, q, task="jev.spec_check", job_id=job_id)["p_yes"]
    except jev.JevError as e:
        raise SearchFailed(f"Couldn't spec-check the product: {e}") from None


def find_for_material(condition_id: str, key: str) -> dict:
    db = get_db()
    cond = db.conditions.find_one({"_id": oid(condition_id)}) if oid(condition_id) else None
    material = next((m for m in (cond or {}).get("materials") or [] if m["key"] == key), None)
    if material is None:
        raise LookupError(f"No material {key!r} on this condition.")
    query = query_for(material)
    qhash = hashlib.sha1(query.lower().encode()).hexdigest()
    doc = db.product_data.find_one({"query_hash": qhash})
    if doc is None:
        if not _key():
            raise Unavailable("Product search needs a Firecrawl key (FIRECRAWL_API_KEY in Infisical).")
        pages = _search(query)
        if not pages:
            raise LookupError(f"No product data found for {query!r}.")
        top = pages[0]
        try:
            attrs = _attrs(f"{top.get('title')}\n{top.get('markdown') or ''}", cond.get("job_id"))
        except TaskFailed as e:
            raise LookupError(f"Couldn't read the product sheet: {e.reason}") from None
        checks = {a: round(_p_mismatch(material, attrs, a, cond.get("job_id")), 3) for a in CHECKED if material.get(a) is not None}
        doc = {"query_hash": qhash, "condition_id": condition_id, "component": key, "url": top.get("url"),
               "title": top.get("title"), "attrs": attrs, "spec_check": checks, "fetched_at": now()}
        try:
            doc["_id"] = db.product_data.insert_one(doc).inserted_id
        except DuplicateKeyError:  # query_hash is unique: a concurrent search for the same query won
            doc = db.product_data.find_one({"query_hash": qhash})
            if doc is None:
                raise
    db.conditions.update_one({"_id": cond["_id"], "materials.key": key}, {"$set": {"materials.$.product_id": str(doc["_id"])}})
    return jsonable(doc)
