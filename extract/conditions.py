"""The conditions library: a takeoff name and a stable colour per condition, and create / edit / delete / duplicate.

    name_for(c) -> 'A1 - 3-5/8" 20ga @ 16" - 1L 5/8" Type X e.s.'
    color_for(db, job_id, "A1") -> "#1f77b4"   (palette slot keyed by type_tag; a free slot when that one's taken)

Writes go through insert_or_review / update_one on the validated collection, so MongoDB's $jsonSchema decides.
"""
import threading
import zlib
from fractions import Fraction

from bson import ObjectId
from pymongo.database import Database
from pymongo.errors import WriteError

from app.db import insert_or_review, oid
from contracts.validate import validate

PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b",
    "#e377c2", "#17becf", "#bcbd22", "#393b79", "#ad494a", "#637939",
]
BOARD_LABELS = {
    "regular": "Regular", "type_x": "Type X", "type_c": "Type C", "moisture_resistant": "MR",
    "mold_resistant": "Mold Res.", "type_x_mold_resistant": "Type X MR", "abuse_resistant": "Abuse Res.",
    "impact_resistant": "Impact Res.", "shaftliner": "Shaftliner", "cement_board": "Cement Bd.",
    "glass_mat_sheathing": "Glass Mat", "sound_dampening": "Sound Damp.",
}
CONTENT = ("type_tag", "studs", "layers_each_side", "sides", "insulation", "fire_rating", "ul_assembly", "stc",
           "height_ft", "name", "color")
_writing = threading.Lock()  # colour pick + insert, so parallel parses don't take the same free colour


class Refused(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _inches(x: float) -> str:
    f = Fraction(x).limit_denominator(16)
    whole, rem = divmod(f.numerator, f.denominator)
    if not rem:
        return f'{whole}"'
    return f'{whole}-{rem}/{f.denominator}"' if whole else f'{rem}/{f.denominator}"'


def name_for(c: dict) -> str:
    studs = c.get("studs") or {}
    stud = []
    if studs.get("size_in"):
        stud.append(_inches(studs["size_in"]))
    if studs.get("gauge"):
        stud.append(f"{studs['gauge']}ga")
    if studs.get("spacing_in"):
        stud.append(f"@ {_inches(studs['spacing_in'])}")
    runs: list[list] = []  # [count, thickness, board type] for consecutive identical layers
    for layer in c.get("layers_each_side") or []:
        key = (layer.get("thickness_in"), layer.get("board_type"))
        if runs and tuple(runs[-1][1:]) == key:
            runs[-1][0] += 1
        else:
            runs.append([1, *key])
    layers = " + ".join(
        f"{n}L {_inches(t) if t else '?'} {BOARD_LABELS.get(b, b or '?')}" for n, t, b in runs
    )
    if layers:
        layers += " o.s." if c.get("sides") == 1 else " e.s."
    return " - ".join(part for part in (c.get("type_tag") or "?", " ".join(stud), layers) if part)


def preferred(tag: str) -> int:
    return zlib.crc32((tag or "").encode()) % len(PALETTE)


def color_for(db: Database, job_id: str, tag: str, exclude: set[str] = frozenset()) -> str:
    """The tag's palette slot, or the next slot no other condition in the job uses. A tag already in the job
    keeps its colour (unless excluded, as for a duplicate)."""
    same = db.conditions.find_one({"job_id": job_id, "type_tag": tag, "color": {"$type": "string"}})
    if same and same["color"] not in exclude:
        return same["color"]
    used = set(db.conditions.distinct("color", {"job_id": job_id})) | set(exclude)
    start = preferred(tag)
    for i in range(len(PALETTE)):
        color = PALETTE[(start + i) % len(PALETTE)]
        if color not in used:
            return color
    return PALETTE[start]


def insert(db: Database, doc: dict, source: str) -> ObjectId | None:
    """insert_or_review with a name and colour filled in when the doc has none."""
    with _writing:
        doc.setdefault("name", name_for(doc))
        doc.setdefault("color", color_for(db, doc.get("job_id"), doc.get("type_tag")))
        return insert_or_review(db, "conditions", doc, source)


def backfill(db: Database, docs: list[dict]) -> list[dict]:
    """Give conditions written before names and colours existed theirs, stored so they stay put."""
    for d in docs:
        if d.get("name") and d.get("color"):
            continue
        with _writing:
            fill = {"name": d.get("name") or name_for(d), "color": d.get("color") or color_for(db, d.get("job_id"), d.get("type_tag"))}
            db.conditions.update_one({"_id": d["_id"]}, {"$set": fill})
        d.update(fill)
    return docs


def _content(fields: dict) -> dict:
    unknown = sorted(set(fields) - set(CONTENT))
    if unknown:
        raise Refused(422, f"not condition fields you can set: {', '.join(unknown)}")
    return dict(fields)


def _rejection(db: Database, doc: dict) -> str:
    review = db.review_queue.find_one({"doc._id": doc.get("_id")}, sort=[("at", -1)])
    return review["reason"] if review else "rejected by the conditions validator"


def create(db: Database, job_id: str, fields: dict, *, routing: dict | None = None, source: dict | None = None):
    """A condition from a person (or a copy). Returns (doc, None), or (None, MongoDB's reason) when the validator
    rejects it, in which case it is in review_queue."""
    doc = {**_content(fields), "job_id": job_id, "source": source or {}, "routing": routing or {"method": "manual"}}
    new_id = insert(db, doc, "extract.condition")
    if new_id is None:
        return None, _rejection(db, doc)
    return db.conditions.find_one({"_id": new_id}), None


def _get(db: Database, condition_id: str) -> dict:
    doc = db.conditions.find_one({"_id": oid(condition_id)})
    if not doc:
        raise Refused(404, "No such condition.")
    return doc


def patch(db: Database, condition_id: str, fields: dict) -> dict:
    old = _get(db, condition_id)
    fields = _content(fields)
    if isinstance(fields.get("studs"), dict):  # edit one stud field without restating the others
        fields["studs"] = {**(old.get("studs") or {}), **fields["studs"]}
    new = {**old, **fields}
    if "name" not in fields and old.get("name") in (None, name_for(old)):  # an auto name follows the edit
        new["name"] = name_for(new)
    errors = validate("conditions", {k: v for k, v in new.items() if k != "_id"})
    if errors:
        raise Refused(422, "; ".join(errors[:8]))
    changed = {k: v for k, v in new.items() if k != "_id" and old.get(k) != v}
    try:
        if changed:
            db.conditions.update_one({"_id": old["_id"]}, {"$set": changed})
    except WriteError as e:
        raise Refused(422, str(e.details.get("errmsg", e)) if e.details else str(e)) from e
    return db.conditions.find_one({"_id": old["_id"]})


def delete(db: Database, condition_id: str) -> None:
    doc = _get(db, condition_id)
    counts = {
        "measurement": db.measurements.count_documents({"condition_id": condition_id}),
        "order": db.orders.count_documents({"condition_id": condition_id}),
    }
    in_use = [f"{n} {what}{'' if n == 1 else 's'}" for what, n in counts.items() if n]
    if in_use:
        raise Refused(409, f"{' and '.join(in_use)} use this condition; remove them first.")
    db.conditions.delete_one({"_id": doc["_id"]})


def duplicate(db: Database, condition_id: str) -> dict:
    old = _get(db, condition_id)
    fields = {k: old[k] for k in CONTENT if k in old and k != "color"}
    fields["name"] = f"{old.get('name') or name_for(old)} (copy)"
    with _writing:
        fields["color"] = color_for(db, old["job_id"], old.get("type_tag"), exclude={old.get("color")})
    doc, reason = create(db, old["job_id"], fields, source=old.get("source"),
                         routing={"method": "duplicate", "duplicate_of": condition_id})
    if doc is None:
        raise Refused(422, reason)
    return doc
