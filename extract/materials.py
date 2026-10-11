"""A condition's materials: what a wall type is built from, one row per material, so each can be priced and
matched to product data. Built from the parsed condition plus the type's callout text (Block.text). No model call."""
import re
from collections import Counter
from fractions import Fraction

from contracts.schemas import GAUGES

BOARD = {
    "regular": "regular", "type_x": "Type X", "type_c": "Type C", "moisture_resistant": "moisture-resistant",
    "mold_resistant": "mold-resistant", "type_x_mold_resistant": "Type X mold-resistant", "abuse_resistant": "abuse-resistant",
    "impact_resistant": "impact-resistant", "shaftliner": "shaftliner", "cement_board": "cement", "glass_mat_sheathing": "glass-mat",
    "sound_dampening": "sound-dampening",
}
NO_INSULATION = {"", "none", "no", "n/a", "na", "-"}
RC = re.compile(r"(\d{2})\s*GA\.?\s*(?:SINGLE\s+LEG\s+)?RESILIENT\s+CHANNEL(?:\s*@\s*(\d+)\s*\"?\s*O\.?C)?")


def _in(v) -> str:
    whole = int(v)
    frac = Fraction(v - whole).limit_denominator(16)
    if not frac:
        return f'{whole}"'
    return f'{frac.numerator}/{frac.denominator}"' if not whole else f'{whole}-{frac.numerator}/{frac.denominator}"'


def _m(key, role, desc, per, source, **attrs) -> dict:
    """Attributes stay even when null: a null gauge means "left to the specs", which the order math assumes."""
    return {"key": key, "role": role, "desc": desc, "per": per, "source": source, **attrs}


def derive(condition: dict, text: str = "") -> list[dict]:
    t = " ".join((text or "").upper().split())
    studs = condition.get("studs") or {}
    size, gauge, spacing = studs.get("size_in"), studs.get("gauge"), studs.get("spacing_in")
    sides = condition.get("sides") or 2
    out = []
    if size:
        g = f" {gauge} ga" if gauge else ""
        out.append(_m("stud", "stud", f"{_in(size)}{g} metal studs" + (f" @ {_in(spacing)} o.c." if spacing else ""), "lf", "drawing",
                      size_in=size, gauge=gauge, spacing_in=spacing))
        out.append(_m("track", "track", f"{_in(size)}{g} track, top and bottom", "lf", "derived", size_in=size, gauge=gauge))
    for (thick, kind), n in Counter((l["thickness_in"], l["board_type"]) for l in condition.get("layers_each_side") or []).items():
        where = "each side" if sides == 2 else "one side"
        out.append(_m(f"board_{thick:g}_{kind}", "board", f"{_in(thick)} {BOARD.get(kind, kind)} gypsum board, {n} layer{'s' * (n > 1)} {where}",
                      "sf", "drawing", thickness_in=thick, board_type=kind, sides=sides))
    insulation = str(condition.get("insulation") or "").strip()
    if insulation.lower() not in NO_INSULATION or "BATT" in t:
        out.append(_m("insulation", "insulation", insulation or "Acoustical batt insulation, full cavity", "sf", "drawing", size_in=size))
    if rc := RC.search(t):
        one_side = "ONE SIDE" in t[rc.end(): rc.end() + 60]
        # The callout may name a gauge the contract has no slot for (24 ga RC); the desc keeps the drawing's
        # number, the attribute goes null so the condition still writes instead of failing validation.
        g = int(rc.group(1))
        out.append(_m("resilient_channel", "resilient_channel", f"{rc.group(1)} ga resilient channel" + (f" @ {rc.group(2)}\" o.c." if rc.group(2) else "")
                      + (", one side" if one_side else ", both sides"), "lf", "drawing",
                      gauge=g if g in GAUGES else None, spacing_in=float(rc.group(2)) if rc.group(2) else None, sides=1 if one_side else 2))
    if "FURRING CHANNEL" in t:
        out.append(_m("furring", "furring", "Furring channel", "lf", "drawing"))
    if "SEALANT" in t:
        rated = bool(condition.get("fire_rating"))
        out.append(_m("sealant_firerated" if rated else "sealant_acoustical", "sealant",
                      "Fire-rated acoustical sealant, top and bottom of wall" if rated else "Acoustical sealant, top and bottom of wall", "lf", "drawing"))
    if "BRACE" in t:
        out.append(_m("bracing", "bracing", "Diagonal bracing back to structure @ 48\" max", "ea", "drawing"))
    return out
