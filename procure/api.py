"""procure lane routes. The integrator includes this router in app/main.py; add endpoints here."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import get_db, jsonable, oid
from procure import find, sources

router = APIRouter(tags=["procure"])


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


@router.post("/api/conditions/{cid}/procure")
def procure_all(cid: str):
    cond = get_db().conditions.find_one({"_id": oid(cid)}) if oid(cid) else None
    if cond is None:
        raise HTTPException(404, "No such condition.")
    out, errors = [], {}
    for m in cond.get("materials") or []:
        try:
            out.append(find.find_for_material(cid, m["key"]))
        except find.Unavailable as e:
            raise HTTPException(409, str(e))
        except find.SearchBlocked as e:  # the key, the credits or the rate limit: the next material would fail the same way
            raise HTTPException(502, str(e))
        except (find.SearchFailed, LookupError) as e:
            errors[m["key"]] = str(e)
    return {"products": out, "errors": errors}


@router.get("/api/conditions/{cid}/products")
def condition_products(cid: str):
    cond = get_db().conditions.find_one({"_id": oid(cid)}) if oid(cid) else None
    ids = [oid(m["product_id"]) for m in (cond or {}).get("materials") or [] if m.get("product_id")]
    return jsonable(list(get_db().product_data.find({"_id": {"$in": ids}})))
