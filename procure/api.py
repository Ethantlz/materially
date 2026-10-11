"""procure lane routes. The integrator includes this router in app/main.py; add endpoints here."""
from fastapi import APIRouter, HTTPException

from app.db import get_db, jsonable, oid
from procure import find

router = APIRouter(tags=["procure"])


@router.post("/api/conditions/{cid}/materials/{key}/products")
def material_products(cid: str, key: str):
    try:
        return find.find_for_material(cid, key)
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
        except LookupError as e:
            errors[m["key"]] = str(e)
    return {"products": out, "errors": errors}


@router.get("/api/conditions/{cid}/products")
def condition_products(cid: str):
    cond = get_db().conditions.find_one({"_id": oid(cid)}) if oid(cid) else None
    ids = [oid(m["product_id"]) for m in (cond or {}).get("materials") or [] if m.get("product_id")]
    return jsonable(list(get_db().product_data.find({"_id": {"$in": ids}})))
