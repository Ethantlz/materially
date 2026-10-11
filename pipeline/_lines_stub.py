"""Stand-in for orders.math.lines_for until H2 merges orders/math.py. Same signature and line shape:

    lines_for(condition, lf, height_ft) -> [{"item", "qty", "unit", "computed_qty", "waste"}, ...]

`item` is a price_book key (format in contracts/seed.py). `computed_qty` is the takeoff quantity; `qty` is what
gets ordered: computed_qty plus waste, rounded up to whole purchase units. The numbers are placeholders, not
H2's order math (SPEC section 7); pipeline/orders.py prefers orders.math whenever it is importable.
"""
import math

STUD_LENGTHS = (8, 9, 10, 12, 14, 16)
BOARD_LENGTHS = (8, 10, 12)
TRACK_LENGTH_FT = 10
WASTE = {"stud": 0.05, "track": 0.05, "board": 0.10, "screws": 0.05, "mud": 0.10, "tape": 0.10, "bead": 0.10, "batt": 0.05}


def _fit(height_ft: float, lengths: tuple[int, ...]) -> int:
    """Shortest stock length that reaches the wall height (the longest when none does)."""
    return next((n for n in lengths if n >= height_ft), lengths[-1])


def _line(item: str, unit: str, computed: float, waste: float) -> dict:
    computed = math.ceil(computed - 1e-9)
    return {"item": item, "qty": math.ceil(computed * (1 + waste) - 1e-9), "unit": unit, "computed_qty": computed, "waste": waste}


def _batt_size(stud_in: float) -> float:
    return 2.5 if stud_in <= 2.5 else 3.5 if stud_in <= 3.625 else 6


def lines_for(condition: dict, lf: float, height_ft: float) -> list[dict]:
    studs = condition["studs"]
    layers = condition["layers_each_side"]
    sides = condition.get("sides") or 2
    size, gauge, spacing = studs["size_in"], studs["gauge"], studs["spacing_in"]

    lines = [
        _line(f"stud_{size:g}_{gauge}ga_{_fit(height_ft, STUD_LENGTHS)}ft", "ea", lf * 12 / spacing + 1, WASTE["stud"]),
        _line(f"track_{size:g}_{gauge}ga_{TRACK_LENGTH_FT}ft", "ea", lf * 2 / TRACK_LENGTH_FT, WASTE["track"]),
    ]

    board_len = _fit(height_ft, BOARD_LENGTHS)
    wall_sf = lf * height_ft * sides
    board: dict[str, float] = {}
    for layer in layers:
        item = f"board_{layer['thickness_in']:g}_{layer['board_type']}_4x{board_len}"
        board[item] = board.get(item, 0) + wall_sf / (4 * board_len)
    lines += [_line(item, "ea", sheets, WASTE["board"]) for item, sheets in board.items()]

    board_sf = wall_sf * len(layers)
    screws = "screws_1.25in" if len(layers) == 1 else "screws_1.625in"
    lines += [
        _line(screws, "box", board_sf * 1.2 / 1000, WASTE["screws"]),
        _line("mud_all_purpose", "box", board_sf / 480, WASTE["mud"]),
        _line("tape_paper_500ft", "roll", board_sf / 1350, WASTE["tape"]),
        _line("corner_bead_10ft", "ea", 2 * math.ceil(height_ft / 10), WASTE["bead"]),
    ]
    if condition.get("insulation"):
        lines.append(_line(f"insulation_batt_{_batt_size(size):g}in", "sf", lf * height_ft, WASTE["batt"]))
    return lines
