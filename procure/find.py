"""Product data for a condition's materials: one Firecrawl (v2) search per material, limited to the supplier sites on
the company's list (procure.sources; none = the open web). One doc per condition and material, cached by query +
supplier list: a repeat pull makes no Firecrawl call, the same search in the same project is reused, a new project
searches afresh. Attributes are read by run_task; each specified attribute is checked by Jev. The paid search is
saved before the Jev checks, so a failed check leaves an unchecked doc rather than a lost search."""
import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction

import httpx
from pymongo.errors import DuplicateKeyError

from app import jev
from app.db import get_db, jsonable, now, oid
from app.router import TaskFailed, run_task
from app.settings import get_settings
from procure import sources

log = logging.getLogger("materially.procure")

FIRECRAWL = "https://api.firecrawl.dev/v2/search"
LIMIT = 3  # results per search; each is scraped to markdown, which is what costs credits
QUERY_MAX = 500  # Firecrawl rejects a longer query
CHECKED = ("thickness_in", "board_type", "gauge", "size_in")  # material attributes a product must match
CHECK_AT_ONCE = 4  # Jev spec checks run at the same time for one product (there are at most len(CHECKED))
BLOCKED = {  # an account-level problem: asking again for the next material would fail the same way
    401: "Firecrawl rejected the key",
    402: "Firecrawl is out of credits",
    429: "Firecrawl rate limit — try again in a minute",
}
BOARD_PHRASE = {
    "regular": "gypsum board", "type_x": "Type X gypsum board", "type_c": "Type C gypsum board",
    "moisture_resistant": "moisture-resistant gypsum board", "mold_resistant": "mold-resistant gypsum board",
    "type_x_mold_resistant": "Type X mold-resistant gypsum board", "abuse_resistant": "abuse-resistant gypsum board",
    "impact_resistant": "impact-resistant gypsum board", "shaftliner": "shaftliner gypsum board", "cement_board": "cement board",
    "glass_mat_sheathing": "glass-mat sheathing", "sound_dampening": "sound-dampening gypsum board",
}


class Unavailable(RuntimeError):
    pass


class SearchFailed(RuntimeError):
    """Firecrawl or Jev failed on a search we did start: the caller answers 502 with this sentence,
    never a bare plain-text 500 the UI can't show."""


class SearchBlocked(SearchFailed):
    """Firecrawl refused the account itself (bad key, no credits, rate limited): a loop over materials stops at the first."""


def _key() -> str:
    return get_settings().firecrawl_api_key


def _inches(v) -> str:
    """3.625 -> 3-5/8", 0.625 -> 5/8"."""
    whole = int(v)
    frac = Fraction(v - whole).limit_denominator(16)
    if not frac:
        return f'{whole}"'
    return f'{frac.numerator}/{frac.denominator}"' if not whole else f'{whole}-{frac.numerator}/{frac.denominator}"'


def query_for(material: dict) -> str:
    """What a supplier would call it: '5/8" Type X gypsum board', '3-5/8" 20 ga steel stud'. Built from the material's
    attributes when it has them, else from the first clause of its description; never the stud spacing ('@ 16" o.c.')."""
    role, size, gauge, thick = material.get("role"), material.get("size_in"), material.get("gauge"), material.get("thickness_in")
    ga = f" {gauge} ga" if gauge else ""
    if role in ("stud", "track") and size:
        q = f'{_inches(size)}{ga} steel {role}'
    elif role == "board" and thick and material.get("board_type") in BOARD_PHRASE:
        q = f'{_inches(thick)} {BOARD_PHRASE[material["board_type"]]}'
    else:
        q = re.split(r"\s*@", str(material.get("desc") or "").split(",")[0])[0].strip()
    return (q or str(material.get("key") or "product"))[:QUERY_MAX].strip()


def _http_url(url) -> str | None:
    """Product links are only ever http(s): a javascript:/data: URL from a search result is dropped."""
    return url if isinstance(url, str) and re.match(r"https?://", url) else None


def _hash(query: str, domains: list[str], scope: str = "") -> str:
    """The cache key of one search: the query and supplier list, within a scope ("condition:<id>" or "job:<id>")."""
    return hashlib.sha1((query.lower() + "|" + ",".join(sorted(domains)) + (f"|{scope}" if scope else "")).encode()).hexdigest()


def _search(query: str, domains=()) -> list[dict]:
    body = {"query": query[:QUERY_MAX], "limit": LIMIT, "scrapeOptions": {"formats": [{"type": "markdown"}]}, "timeout": 60000}
    if domains:
        body["includeDomains"] = list(domains)
    try:
        r = httpx.post(FIRECRAWL, headers={"Authorization": f"Bearer {_key()}"}, json=body, timeout=70)
    except httpx.TimeoutException:
        raise SearchFailed("Firecrawl took too long to answer — try again") from None
    except httpx.HTTPError:
        raise SearchFailed("Couldn't reach Firecrawl") from None
    status = r.status_code
    if status in BLOCKED:
        raise SearchBlocked(BLOCKED[status])
    if status == 408:
        raise SearchFailed("Firecrawl timed out searching — try again")
    try:
        payload = r.json()
    except ValueError:
        payload = None
    ok = 200 <= status < 300
    if not isinstance(payload, dict):
        raise SearchFailed("Firecrawl answered with something that wasn't JSON" if ok else f"Firecrawl said {status}")
    why = str(payload.get("error") or "").strip()[:200]
    if not ok:
        raise SearchFailed(f"Firecrawl said {status}" + (f": {why}" if why else ""))
    if payload.get("success") is False:
        raise SearchFailed("Firecrawl couldn't search" + (f": {why}" if why else ""))
    data = payload.get("data")
    web = data.get("web") if isinstance(data, dict) else None
    return [p for p in web if isinstance(p, dict)] if isinstance(web, list) else []


def _attrs(text: str, job_id: str) -> dict:
    return run_task("extract_product_attrs", text[:12000], "product_attrs", job_id=job_id)["result"]


def _p_mismatch(material: dict, attrs: dict, attr: str, job_id: str) -> float:
    q = f"Does this product fail the specified {attr}: {material.get(attr)}?"
    try:
        return jev.noul({"specified": material, "product": attrs}, q, task="jev.spec_check", job_id=job_id)["p_yes"]
    except jev.JevError as e:
        raise SearchFailed(f"Couldn't spec-check the product: {e}") from None


def _checks(material: dict, attrs: dict, job_id: str) -> dict:
    """p(mismatch) for each specified attribute, the Jev calls running concurrently. Every call finishes before this
    returns; if any failed, the first failure in CHECKED order is raised (as the one-by-one loop did)."""
    wanted = [a for a in CHECKED if material.get(a) is not None]
    if not wanted:
        return {}
    with ThreadPoolExecutor(max_workers=min(CHECK_AT_ONCE, len(wanted))) as pool:
        futures = {a: pool.submit(_p_mismatch, material, attrs, a, job_id) for a in wanted}
    return {a: round(f.result(), 3) for a, f in futures.items()}


def _spec_check(db, doc: dict, material: dict, job_id: str) -> dict:
    """Ask Jev about each specified attribute and store the answers with checked True. If Jev fails the doc stays
    unchecked (the paid search is kept) and comes back with a `check_error`, which is never stored."""
    try:
        checks = _checks(material, doc.get("attrs") or {}, job_id)
    except SearchFailed as e:
        log.warning("spec check failed for product %s: %s", doc["_id"], e)
        return {**doc, "check_error": str(e)}
    db.product_data.update_one({"_id": doc["_id"]}, {"$set": {"spec_check": checks, "checked": True}})
    return {**doc, "spec_check": checks, "checked": True}


def find_for_material(condition_id: str, key: str) -> dict:
    db = get_db()
    cond = db.conditions.find_one({"_id": oid(condition_id)}) if oid(condition_id) else None
    material = next((m for m in (cond or {}).get("materials") or [] if m["key"] == key), None)
    if material is None:
        raise LookupError(f"No material {key!r} on this condition.")
    query = query_for(material)
    domains = sorted({s["domain"] for s in sources.list_sources()})  # no suppliers yet = the open web
    # One doc per condition and material: its spec checks are against this condition's material, and a new project
    # searches afresh instead of showing another project's results. The same search earlier in this project is
    # reused (search_key), so a board that six wall types share costs one Firecrawl search.
    qhash, skey = _hash(query, domains, f"condition:{condition_id}"), _hash(query, domains, f"job:{cond.get('job_id')}")
    doc = db.product_data.find_one({"query_hash": qhash})
    if doc is None:
        found = db.product_data.find_one({"search_key": skey})
        if found:
            url, title, attrs = found.get("url"), found.get("title"), found.get("attrs") or {}
        else:
            if not _key():
                raise Unavailable("Product search needs a Firecrawl key (FIRECRAWL_API_KEY in Infisical).")
            pages = _search(query, domains)
            if not pages:
                raise LookupError(f"No product data found for {query!r}.")
            top = next((p for p in pages if p.get("markdown")), pages[0])
            try:
                attrs = _attrs(f"{top.get('title') or ''}\n{top.get('markdown') or top.get('description') or ''}", cond.get("job_id"))
            except TaskFailed as e:
                raise LookupError(f"Couldn't read the product sheet: {e.reason}") from None
            url, title = _http_url(top.get("url")), top.get("title")
        # Saved before the Jev checks: a failed check must not throw away a search that was paid for.
        doc = {"query_hash": qhash, "search_key": skey, "condition_id": condition_id, "component": key, "url": url, "title": title,
               "domains": domains, "attrs": attrs, "spec_check": {}, "checked": False, "fetched_at": now()}
        try:
            doc["_id"] = db.product_data.insert_one(doc).inserted_id
        except DuplicateKeyError:  # query_hash is unique: a concurrent search for the same query won
            doc = db.product_data.find_one({"query_hash": qhash})
            if doc is None:
                raise
    if doc.get("checked") is False:  # new, or saved earlier with its checks failed: Jev only, no new search
        doc = _spec_check(db, doc, material, cond.get("job_id"))
    db.conditions.update_one({"_id": cond["_id"], "materials.key": key}, {"$set": {"materials.$.product_id": str(doc["_id"])}})
    return jsonable(doc)
