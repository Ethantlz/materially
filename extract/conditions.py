"""The conditions library: a takeoff name and a stable colour per condition, and create / edit / delete / duplicate.

    name_for(c) -> 'A1 - 3-5/8" 20ga @ 16" - 1L 5/8" Type X e.s.'
    color_for(db, job_id, "A1") -> "#1f77b4"   (palette slot keyed by type_tag; a free slot when that one's taken)

Writes go through insert_or_review / update_one on the validated collection, so MongoDB's $jsonSchema decides.
"""
import copy
import threading
import zlib
from fractions import Fraction

from bson import ObjectId
from pymongo.database import Database
from pymongo.errors import WriteError

from app.db import insert_or_review, oid
from contracts.validate import validate
from extract.materials import derive

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
# What a sub-condition (the same wall type at another height) takes from its parent, and a re-scan of the parent
# gives it again. Its _id, name, colour, height_ft and parent_id are its own.
ASSEMBLY = ("type_tag", "studs", "layers_each_side", "sides", "insulation", "fire_rating", "ul_assembly", "stc",
            "materials", "source")
HEIGHT_FT = (1, 60)  # the schema's height_ft range
_writing = threading.Lock()  # colour pick + insert, so parallel parses don't take the same free colour
# Trade sizes a metric drawing's conversions land near (92 mm -> 3.622", 400 mm -> 15.75", 16 mm -> 0.63"),
# with how close (fraction of the size) a value must be to snap.
NOMINAL = {
    "size_in": ([1.625, 2.5, 3.5, 3.625, 4, 6, 8, 10], 0.015),
    "spacing_in": ([12, 16, 19.2, 24], 0.025),
    "thickness_in": ([0.25, 0.375, 0.5, 0.625, 0.75, 1], 0.04),
}


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


def _snap(value, field: str):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return value
    sizes, tol = NOMINAL[field]
    best = min(sizes, key=lambda size: abs(size - value))
    return best if abs(best - value) <= tol * best else value


def nominal(result: dict) -> tuple[dict, dict]:
    """A copy of a parsed condition with stud size, spacing and board thickness snapped to the trade size they
    convert from, and {field path: value as parsed} for each one that moved."""
    out, changed = copy.deepcopy(result), {}
    studs = out.get("studs")
    for key in ("size_in", "spacing_in"):
        if isinstance(studs, dict) and _snap(studs.get(key), key) != studs.get(key):
            changed[f"studs.{key}"], studs[key] = studs[key], _snap(studs[key], key)
    for i, layer in enumerate(out.get("layers_each_side") or []):
        if isinstance(layer, dict) and _snap(layer.get("thickness_in"), "thickness_in") != layer.get("thickness_in"):
            changed[f"layers_each_side.{i}.thickness_in"] = layer["thickness_in"]
            layer["thickness_in"] = _snap(layer["thickness_in"], "thickness_in")
    return out, changed


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


def insert(db: Database, doc: dict, source: str, exclude: set[str] = frozenset()) -> ObjectId | None:
    """insert_or_review with a name and colour filled in when the doc has none (a colour not in `exclude`)."""
    with _writing:
        doc.setdefault("name", name_for(doc))
        doc.setdefault("color", color_for(db, doc.get("job_id"), doc.get("type_tag"), exclude))
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


BUILD_ROLES = ("stud", "track", "board")  # the material rows derive() makes from studs / layers / sides alone


def rebuilt_materials(materials: list[dict], condition: dict) -> list[dict]:
    """A condition's materials after its build was edited by hand: the stud, track and board rows again from the
    edited build, the rows only the callout text gave (insulation, resilient channel, sealant …) as they were. A row
    keeps the product found for it only while it is still the same material (key and attributes)."""
    before = {m.get("key"): m for m in materials}
    out = []
    for m in (m for m in derive(condition) if m["role"] in BUILD_ROLES):
        was = before.get(m["key"]) or {}
        same = was and all(was.get(k) == v for k, v in m.items() if k not in ("desc", "source"))
        out.append({**m, "product_id": was["product_id"]} if same and was.get("product_id") else m)
    stud_in = (condition.get("studs") or {}).get("size_in")
    for m in (m for m in materials if m.get("role") not in BUILD_ROLES):
        if m.get("role") == "insulation" and "size_in" in m and m["size_in"] != stud_in:  # a batt fills the stud cavity
            m = {**m, "size_in": stud_in, "product_id": None}
        out.append(m)
    return out


def patch(db: Database, condition_id: str, fields: dict) -> dict:
    """Edit a condition. A height (sub-condition) takes its build from its wall type, so only its own name, colour and
    height_ft change here; an edit to a wall type's build rebuilds its materials and goes to its heights too."""
    old = _get(db, condition_id)
    fields = _content(fields)
    if isinstance(fields.get("studs"), dict):  # edit one stud field without restating the others
        fields["studs"] = {**(old.get("studs") or {}), **fields["studs"]}
    if old.get("parent_id") and any(fields[k] != old.get(k) for k in set(fields) & set(ASSEMBLY)):
        raise Refused(422, "A height takes its build from its wall type; edit the wall type instead.")
    new = {**old, **fields}
    if "name" not in fields and old.get("name") in (None, name_for(old)):  # an auto name follows the edit
        new["name"] = name_for(new)
    if old.get("materials") and any(new.get(k) != old.get(k) for k in ("studs", "layers_each_side", "sides")):
        new["materials"] = rebuilt_materials(old["materials"], new)
    errors = validate("conditions", {k: v for k, v in new.items() if k != "_id"})
    if errors:
        raise Refused(422, "; ".join(errors[:8]))
    changed = {k: v for k, v in new.items() if k != "_id" and old.get(k) != v}
    try:
        if changed:
            db.conditions.update_one({"_id": old["_id"]}, {"$set": changed})
    except WriteError as e:
        raise Refused(422, str(e.details.get("errmsg", e)) if e.details else str(e)) from e
    _heights_follow(db, old, new, changed)
    return db.conditions.find_one({"_id": old["_id"]})


def _heights_follow(db: Database, old: dict, new: dict, changed: dict) -> None:
    """A wall type's edited build goes to its heights, as a re-scan's does (extract.analyze._sync_heights). Each
    height keeps its own _id, colour, height_ft and the products found for materials that didn't change, and an
    auto name ("<wall type's name> @ 10'") follows the wall type's."""
    build = {k: new[k] for k in ASSEMBLY if k in changed and k != "materials"}
    if not build and "name" not in changed:
        return
    for child in db.conditions.find({"parent_id": str(old["_id"])}):
        set_ = dict(build)
        if "materials" in changed:
            set_["materials"] = rebuilt_materials(child.get("materials") or new["materials"], {**child, **build})
        h = child.get("height_ft")
        if h is not None and child.get("name") == f"{old.get('name') or name_for(old)} @ {h:g}'":
            set_["name"] = f"{new.get('name') or name_for(new)} @ {h:g}'"
        if set_:
            db.conditions.update_one({"_id": child["_id"]}, {"$set": set_})


def delete(db: Database, condition_id: str) -> None:
    doc = _get(db, condition_id)
    if db.conditions.count_documents({"parent_id": str(doc["_id"])}):
        raise Refused(409, "Delete its heights first.")
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


def add_subcondition(db: Database, parent_id: str, height_ft, name: str | None = None) -> dict:
    """The parent's wall type at another height: a condition of its own (own _id, name, colour, height_ft) that
    copies the parent's assembly and points back at it with parent_id. Measurements, orders and alternates
    reference it like any condition. One level only: a sub-condition takes no heights of its own."""
    parent = _get(db, parent_id)
    if parent.get("parent_id"):
        raise Refused(422, "A height can't have heights of its own; add the height to its wall type.")
    lo, hi = HEIGHT_FT
    if isinstance(height_ft, bool) or not isinstance(height_ft, (int, float)) or not lo <= height_ft <= hi:
        raise Refused(422, f"height_ft must be a number of feet from {lo} to {hi}.")
    if name is not None and not isinstance(name, str):
        raise Refused(422, "name must be text.")
    doc = {k: copy.deepcopy(parent[k]) for k in ASSEMBLY if k in parent}
    doc.update(
        job_id=parent["job_id"], parent_id=str(parent["_id"]), height_ft=height_ft,
        name=(name or "").strip() or f"{parent.get('name') or name_for(parent)} @ {height_ft:g}'",
        routing={"method": "subcondition", "parent_id": str(parent["_id"])},
    )
    # a colour no condition of this wall type uses yet, so the heights read apart on the drawing
    taken = set(db.conditions.distinct("color", {"job_id": parent["job_id"], "type_tag": parent.get("type_tag")}))
    new_id = insert(db, doc, "extract.condition", exclude=taken)
    if new_id is None:
        raise Refused(422, _rejection(db, doc))
    return db.conditions.find_one({"_id": new_id})
