"""The company-wide list of supplier sites that product searches are limited to (Firecrawl `includeDomains`).
One row per hostname: the same site typed twice, with or without "www." or a path, is one supplier."""
import re
from urllib.parse import urlsplit, urlunsplit

from pymongo.errors import DuplicateKeyError

from app.db import get_db, jsonable, now, oid

HOST = re.compile(r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
BAD_ADDRESS = "That doesn't look like a website address. Try something like gypsum-supply.com."


def _normalise(url: str) -> tuple[str, str]:
    """('https://www.Example.com/x?y#z') -> ('https://www.example.com/x?y', 'example.com'); ValueError when there's no site in it."""
    raw = (url or "").strip()
    if not raw:
        raise ValueError(BAD_ADDRESS)
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
        host, port = (parts.hostname or "").lower().encode("idna").decode("ascii"), parts.port  # bücher.de -> xn--bcher-kva.de
    except ValueError:  # (UnicodeError is one)
        raise ValueError(BAD_ADDRESS) from None
    if parts.scheme.lower() not in ("http", "https") or not HOST.match(host):
        raise ValueError(BAD_ADDRESS)
    netloc = host + (f":{port}" if port else "")  # no user:password@
    clean = urlunsplit((parts.scheme.lower(), netloc, parts.path, parts.query, ""))
    return clean, host.removeprefix("www.")


def add_source(url: str, name: str | None = None) -> dict:
    """Add a supplier site. A domain that is already on the list returns the existing row, untouched."""
    clean, domain = _normalise(url)
    db = get_db()
    doc = db.product_sources.find_one({"domain": domain})
    if doc is None:
        doc = {"url": clean, "domain": domain, "name": (name or "").strip() or None, "created_at": now()}
        try:
            doc["_id"] = db.product_sources.insert_one(doc).inserted_id
        except DuplicateKeyError:  # `domain` is unique: a concurrent add of the same site won
            doc = db.product_sources.find_one({"domain": domain})
            if doc is None:
                raise
    return jsonable(doc)


def list_sources() -> list[dict]:
    return jsonable(list(get_db().product_sources.find({}).sort([("created_at", 1), ("domain", 1)])))


def delete_source(source_id: str) -> None:
    if not oid(source_id) or get_db().product_sources.delete_one({"_id": oid(source_id)}).deleted_count == 0:
        raise LookupError("No such supplier.")
