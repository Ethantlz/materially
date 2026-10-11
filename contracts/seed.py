"""Seed rows: model_policy (the router reads it live) and a made-up price_book.

price_book item keys are the contract between orders.math (H2) and the $lookup (lane 2).
Numbers are formatted with :g (3.625 -> "3.625", 6.0 -> "6"):
    stud_{size_in}_{gauge}ga_{length_ft}ft    ea     lengths 8, 9, 10, 12, 14, 16
    track_{size_in}_{gauge}ga_10ft            ea
    board_{thickness_in}_{board_type}_4x{L}   ea     L = 8, 10, 12
    screws_1.25in | screws_1.625in            box (1000)
    mud_all_purpose                           box (4.5 gal)
    mud_setting_45                            bag
    tape_paper_500ft                          roll
    corner_bead_10ft                          ea
    insulation_batt_{size_in}in               sf     2.5, 3.5, 6
    labor_framing_hr | labor_hanging_hr | labor_finishing_hr   hr
    resilient_channel_25ga_12ft               ea
    sealant_acoustical_28oz | sealant_firerated_10oz   tube
"""
from contracts.schemas import BOARD_TYPES

DEEPSEEK = "deepseek-ai/DeepSeek-V4.1-Flash"
SONNET = "claude-sonnet-5-5"
SUPPLIER = "Sample Supply Co"

MODEL_POLICY = [
    {"task": task, "tier": "quick", "model": DEEPSEEK, "retry_model": SONNET, "enabled": True}
    for task in ("parse_partition", "extract_product_attrs", "price_extra", "base_scope_check", "backcharge")
]

STUD_SIZES = {1.625: 0.42, 2.5: 0.55, 3.625: 0.70, 4: 0.76, 6: 0.98}  # $/ft at 25 ga
GAUGE_FACTOR = {25: 1.0, 20: 1.45, 18: 1.9, 16: 2.4}
STUD_LENGTHS = [8, 9, 10, 12, 14, 16]
BOARD_SF = {  # $/sf at 5/8"; 1/2" is 0.9x
    "regular": 0.42,
    "type_x": 0.54,
    "type_c": 0.66,
    "moisture_resistant": 0.58,
    "mold_resistant": 0.62,
    "type_x_mold_resistant": 0.70,
    "abuse_resistant": 0.88,
    "impact_resistant": 1.15,
    "shaftliner": 0.95,
    "cement_board": 1.05,
    "glass_mat_sheathing": 0.80,
    "sound_dampening": 2.40,
}
assert set(BOARD_SF) == set(BOARD_TYPES)


def _row(item: str, unit: str, unit_cost: float) -> dict:
    return {"item": item, "unit": unit, "unit_cost": round(unit_cost, 2), "supplier": SUPPLIER}


def price_book() -> list[dict]:
    rows = []
    for size, per_ft in STUD_SIZES.items():
        for gauge, factor in GAUGE_FACTOR.items():
            for length in STUD_LENGTHS:
                rows.append(_row(f"stud_{size:g}_{gauge}ga_{length}ft", "ea", per_ft * factor * length))
            rows.append(_row(f"track_{size:g}_{gauge}ga_10ft", "ea", per_ft * factor * 10))
    for board_type, per_sf in BOARD_SF.items():
        for thickness, t_factor in ((0.5, 0.9), (0.625, 1.0)):
            for length in (8, 10, 12):
                rows.append(_row(f"board_{thickness:g}_{board_type}_4x{length}", "ea", per_sf * t_factor * 4 * length))
    rows += [
        _row("screws_1.25in", "box", 28.0),
        _row("screws_1.625in", "box", 34.0),
        _row("mud_all_purpose", "box", 22.0),
        _row("mud_setting_45", "bag", 19.0),
        _row("tape_paper_500ft", "roll", 6.0),
        _row("corner_bead_10ft", "ea", 3.4),
        _row("insulation_batt_2.5in", "sf", 0.70),
        _row("insulation_batt_3.5in", "sf", 0.85),
        _row("insulation_batt_6in", "sf", 1.25),
        _row("labor_framing_hr", "hr", 85.0),
        _row("labor_hanging_hr", "hr", 80.0),
        _row("labor_finishing_hr", "hr", 78.0),
        _row("resilient_channel_25ga_12ft", "ea", 6.40),
        _row("sealant_acoustical_28oz", "tube", 9.50),
        _row("sealant_firerated_10oz", "tube", 12.75),
    ]
    return rows
