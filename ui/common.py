"""Helpers shared by the UI pages and the takeoff stand-ins: inch formatting, condition names and colours,
sheet disciplines."""
import re
from fractions import Fraction

from contracts.schemas import BOARD_TYPES

BOARD_LABELS = {
    "regular": "Regular", "type_x": "Type X", "type_c": "Type C", "moisture_resistant": "Moisture resistant",
    "mold_resistant": "Mold resistant", "type_x_mold_resistant": "Type X mold resistant",
    "abuse_resistant": "Abuse resistant", "impact_resistant": "Impact resistant", "shaftliner": "Shaftliner",
    "cement_board": "Cement board", "glass_mat_sheathing": "Glass-mat sheathing", "sound_dampening": "Sound dampening",
}
assert set(BOARD_LABELS) == set(BOARD_TYPES)

# Fallback for conditions written before lane 1 stamps a name and colour. Lane 1's palette wins when present.
PALETTE = ["#e8590c", "#2f9e44", "#7950f2", "#1098ad", "#d6336c", "#f59f00", "#4263eb", "#82c91e", "#c2255c", "#0ca678"]

DISCIPLINES = {
    "G": "General", "A": "Architectural", "I": "Interiors", "S": "Structural", "M": "Mechanical", "E": "Electrical",
    "P": "Plumbing", "F": "Fire protection", "C": "Civil", "L": "Landscape", "T": "Telecom", "D": "Demolition",
}


def inches(v) -> str:
    """3.625 -> 3-5/8", 0.625 -> 5/8", 6 -> 6"."""
    if v is None:
        return "—"
    whole = int(v)
    frac = Fraction(v - whole).limit_denominator(16)
    if not frac:
        return f'{whole}"'
    return f'{frac.numerator}/{frac.denominator}"' if not whole else f'{whole}-{frac.numerator}/{frac.denominator}"'


def reason_summary(reason: str) -> str:
    """One readable line from MongoDB's 'Document failed validation: {details}' (insert_or_review's reason).
    Other reasons pass through unchanged."""
    if "failed validation" not in (reason or ""):
        return reason or ""
    parts = []
    for m in re.finditer(r"'reason': '([^']+)', 'consideredValue': ([^}]+)}", reason):
        prop = re.findall(r"'propertyName': '(\w+)'", reason[: m.start()])
        why = "not allowed" if "enum" in m.group(1) else m.group(1)
        parts.append(f"{prop[-1] if prop else 'value'}: {m.group(2).strip()} {why}")
    for m in re.finditer(r"'missingProperties': \[([^\]]*)\]", reason):
        parts.append("missing: " + m.group(1).replace("'", ""))
    return "Rejected by MongoDB's validator · " + " · ".join(parts) if parts else reason


def discipline(sheet_no: str | None) -> dict:
    """A-101 -> {"code": "A", "name": "Architectural"}; sheets without a number land in 'Other'."""
    m = re.match(r"\s*([A-Za-z])", sheet_no or "")
    code = m.group(1).upper() if m else "·"
    return {"code": code, "name": DISCIPLINES.get(code, "Other" if code == "·" else code)}


def stud_text(studs: dict | None, spec: str = "per specs") -> str:
    """3-5/8" 20 ga @ 16" o.c.; gauge and spacing are null when only the specs give them: 3-5/8" · gauge, o.c. per specs."""
    s = studs or {}
    size, gauge, spacing = inches(s.get("size_in")), s.get("gauge"), s.get("spacing_in")
    if gauge and spacing:
        return f'{size} {gauge} ga @ {inches(spacing)} o.c.'
    if gauge:
        return f'{size} {gauge} ga · o.c. {spec}'
    if spacing:
        return f'{size} @ {inches(spacing)} o.c. · gauge {spec}'
    return f'{size} · gauge, o.c. {spec}'


def condition_name(c: dict) -> str:
    """A1 - 3-5/8" 20ga @ 16" - 1L 5/8" Type X e.s. (the same shape lane 1 stores in `name`)."""
    studs = c.get("studs") or {}
    layers = c.get("layers_each_side") or []
    parts = [c.get("type_tag") or "?"]
    if studs:
        gauge, spacing = studs.get("gauge"), studs.get("spacing_in")
        parts.append(inches(studs.get("size_in")) + (f" {gauge}ga" if gauge else "") + (f' @ {inches(spacing)}' if spacing else "") + ("" if gauge and spacing else " (per specs)"))
    if layers:
        first = layers[0]
        parts.append(f'{len(layers)}L {inches(first.get("thickness_in"))} {BOARD_LABELS.get(first.get("board_type"), first.get("board_type"))}'
                     + (" e.s." if c.get("sides") == 2 else ""))
    return " - ".join(parts)


def condition_view(c: dict, fallback_index: int = 0) -> dict:
    """A condition for the canvas: name and colour filled in when lane 1 hasn't stored them."""
    src = c.get("source") or {}
    return {
        "id": c["id"], "job_id": c.get("job_id"), "type_tag": c.get("type_tag"),
        "name": c.get("name") or condition_name(c),
        "color": c.get("color") or PALETTE[fallback_index % len(PALETTE)],
        "height_ft": c.get("height_ft"), "parent_id": c.get("parent_id"),  # parent_id: set on a sub-condition (a height)
        "sides": c.get("sides"), "fire_rating": c.get("fire_rating"),
        # What the type is built from, so the takeoff can expand a condition into its studs, track, board …
        "materials": c.get("materials") or [],
        "source": {"sheet": src.get("sheet"), "page": src.get("page"), "bbox": src.get("bbox")} if src else None,
    }


def _heights_after_parents(conditions: list[dict]) -> list[dict]:
    """Top-level conditions by type tag, each followed by its sub-conditions (heights) from low to high. A
    sub-condition whose parent isn't in the list is listed on its own."""
    tops = {c.get("id") for c in conditions if not c.get("parent_id")}
    kids: dict[str, list[dict]] = {}
    for c in conditions:
        if c.get("parent_id") in tops:
            kids.setdefault(c["parent_id"], []).append(c)
    alone = [c for c in conditions if not (c.get("parent_id") in tops)]
    out = []
    for c in sorted(alone, key=lambda c: (c.get("type_tag") or "", c.get("id") or "")):
        out.append(c)
        out += sorted(kids.get(c.get("id"), []), key=lambda k: (k.get("height_ft") or 0, k.get("id") or ""))
    return out


def condition_views(conditions: list[dict]) -> list[dict]:
    """Sorted by type tag, each wall type's heights right after it. A condition without a stored colour takes the
    next palette colour nobody uses."""
    ordered = _heights_after_parents(conditions)
    used = {c["color"] for c in ordered if c.get("color")}
    spare = [p for p in PALETTE if p not in used] or PALETTE
    out, i = [], 0
    for c in ordered:
        if c.get("color"):
            out.append(condition_view(c))
        else:
            out.append(condition_view({**c, "color": spare[i % len(spare)]}))
            i += 1
    return out


def next_color(used: list[str]) -> str:
    return next((p for p in PALETTE if p not in used), PALETTE[len(used) % len(PALETTE)])
