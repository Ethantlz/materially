"""Order math: a wall condition x LF x height -> material lines, as price_book items (SPEC section 7).

    lines_for(condition, lf, height_ft) -> [{"item", "qty", "unit", "computed_qty", "waste"}, ...]

`computed_qty` is the takeoff quantity before waste; `qty` is what gets ordered: computed_qty plus waste,
rounded up to a whole purchase unit. The over-purchase guard (pipeline.orders.guard) flags a line whose qty
exceeds that same rounded-up allowance, so lines built here never trip it.

Every number is in CONSTANTS. These are placeholders: swap in your own and nothing else changes.
`item` keys follow contracts/seed.py (stud_{size}_{gauge}ga_{length}ft, board_{thickness}_{type}_4x{L}, ...).
"""
import math

CONSTANTS = {
    # Studs: ceil(LF x 12 / spacing_in) + 1, cut from the shortest stock length that reaches the wall height.
    "stud_stock_lengths_ft": (8, 9, 10, 12, 14, 16),
    "stud_extra": 1,
    # Track: top and bottom runs, in stock lengths.
    "track_runs": 2,
    "track_length_ft": 10,
    # Board: ceil(LF x height x layers x sides / sheet area), a 4 x L sheet with L the shortest stock >= height.
    "board_sheet_width_ft": 4,
    "board_stock_lengths_ft": (8, 10, 12),
    # Screws: per square foot of board (every layer, every side), sold by the box.
    "screw_item": "screws_1.25in",
    "screws_per_board_sf": 1,
    "screws_per_box": 1000,
    # Finishing, on the finished face area (LF x height x sides; layers don't add finished area).
    "mud_item": "mud_all_purpose",
    "mud_sf_per_box": 450,
    "tape_item": "tape_paper_500ft",
    "tape_ft_per_finished_sf": 0.38,
    "tape_ft_per_roll": 500,
    # Insulation: LF x height square feet, batts sized to the stud depth, only when the condition has insulation.
    "insulation_sizes_in": (2.5, 3.5, 6),
    "no_insulation_words": ("", "none", "no", "n/a", "na", "-"),
    # Corner bead: 0 unless the condition carries corner_bead_lf (linear feet of outside corners).
    "corner_bead_item": "corner_bead_10ft",
    "corner_bead_length_ft": 10,
    # Waste, as a fraction of the takeoff quantity.
    "waste": {"stud": 0.10, "track": 0.05, "board": 0.10, "screws": 0.05, "mud": 0.10, "tape": 0.05, "insulation": 0.05, "corner_bead": 0.0},
}
K = CONSTANTS


def _stock(height_ft: float, lengths: tuple[int, ...]) -> int:
    """Shortest stock length that reaches the wall height (the longest one when none does)."""
    return next((n for n in lengths if n >= height_ft), lengths[-1])


def _line(item: str, unit: str, raw: float, waste: float) -> dict:
    computed = round(raw, 3)
    # Same rounding as the guard: ceil(computed x (1 + waste)), with a hair off so 100 x 1.1 stays 110.
    return {"item": item, "qty": math.ceil(computed * (1 + waste) - 1e-9), "unit": unit, "computed_qty": computed, "waste": waste}


def _batt(stud_in: float) -> float:
    """The thickest batt that fits the stud depth (the thinnest when none does)."""
    return max((s for s in K["insulation_sizes_in"] if s <= stud_in + 0.2), default=K["insulation_sizes_in"][0])


def _has_insulation(condition: dict) -> bool:
    return str(condition.get("insulation") or "").strip().lower() not in K["no_insulation_words"]


def lines_for(condition: dict, lf: float, height_ft: float) -> list[dict]:
    try:
        studs, layers = condition["studs"], condition["layers_each_side"]
        size, gauge, spacing = studs["size_in"], studs["gauge"], studs["spacing_in"]
    except (KeyError, TypeError):
        raise ValueError("condition needs studs {size_in, gauge, spacing_in} and layers_each_side") from None
    if not layers:
        raise ValueError("condition has no layers_each_side")
    sides = condition.get("sides") or 2
    waste = K["waste"]

    lines = [
        _line(
            f"stud_{size:g}_{gauge}ga_{_stock(height_ft, K['stud_stock_lengths_ft'])}ft", "ea",
            math.ceil(lf * 12 / spacing - 1e-9) + K["stud_extra"], waste["stud"],
        ),
        _line(
            f"track_{size:g}_{gauge}ga_{K['track_length_ft']}ft", "ea",
            math.ceil(lf * K["track_runs"] / K["track_length_ft"] - 1e-9), waste["track"],
        ),
    ]

    sheet_len = _stock(height_ft, K["board_stock_lengths_ft"])
    sheet_sf = K["board_sheet_width_ft"] * sheet_len
    sheets: dict[str, int] = {}  # board item -> layers of it on each side
    for layer in layers:
        item = f"board_{layer['thickness_in']:g}_{layer['board_type']}_4x{sheet_len}"
        sheets[item] = sheets.get(item, 0) + 1
    face_sf = lf * height_ft * sides  # one layer's worth of board, both sides
    for item, n_layers in sheets.items():
        lines.append(_line(item, "ea", math.ceil(face_sf * n_layers / sheet_sf - 1e-9), waste["board"]))

    board_sf = face_sf * len(layers)
    lines += [
        _line(K["screw_item"], "box", board_sf * K["screws_per_board_sf"] / K["screws_per_box"], waste["screws"]),
        _line(K["mud_item"], "box", face_sf / K["mud_sf_per_box"], waste["mud"]),
        _line(K["tape_item"], "roll", face_sf * K["tape_ft_per_finished_sf"] / K["tape_ft_per_roll"], waste["tape"]),
    ]
    if _has_insulation(condition):
        lines.append(_line(f"insulation_batt_{_batt(size):g}in", "sf", lf * height_ft, waste["insulation"]))
    bead_lf = condition.get("corner_bead_lf") or 0
    if bead_lf > 0:
        lines.append(_line(K["corner_bead_item"], "ea", math.ceil(bead_lf / K["corner_bead_length_ft"] - 1e-9), waste["corner_bead"]))
    return lines
