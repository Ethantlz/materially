"""orders.math.lines_for: the hand-worked case, stock lengths, layers, and the contract line shape."""
import math

import pytest

from contracts.samples import SAMPLES
from orders import math as order_math
from orders.math import lines_for

A1 = {k: v for k, v in SAMPLES["conditions"].items() if k != "_id"}  # 3-5/8" 20 ga @ 16", 1 layer 5/8" Type X each side


def by_item(lines):
    return {l["item"]: l for l in lines}


def test_a1_100_lf_10_ft_by_hand():
    """100 LF x 10 ft, 3-5/8" 20 ga @ 16", one layer 5/8" Type X each side, both sides, with insulation.
    studs   ceil(100 x 12 / 16) + 1 = 76          x 1.10 = 83.6  -> 84   (10 ft stock)
    track   ceil(100 x 2 / 10)      = 20          x 1.05 = 21    -> 21
    board   100 x 10 x 1 x 2 / 40   = 50          x 1.10 = 55    -> 55   (4x10)
    screws  2000 sf of board / 1000 = 2 boxes     x 1.05 = 2.1   -> 3
    mud     2000 finished sf / 450  = 4.444 boxes x 1.10 = 4.889 -> 5
    tape    2000 x 0.38 / 500       = 1.52 rolls  x 1.05 = 1.596 -> 2
    batts   100 x 10                = 1000 sf     x 1.05 = 1050  -> 1050 (3-1/2" for a 3-5/8" stud)
    corner bead: none unless passed in."""
    lines = lines_for(A1, 100, 10)

    assert [(l["item"], l["unit"], l["computed_qty"], l["waste"], l["qty"]) for l in lines] == [
        ("stud_3.625_20ga_10ft", "ea", 76, 0.10, 84),
        ("track_3.625_20ga_10ft", "ea", 20, 0.05, 21),
        ("board_0.625_type_x_4x10", "ea", 50, 0.10, 55),
        ("screws_1.25in", "box", 2, 0.05, 3),
        ("mud_all_purpose", "box", 4.444, 0.10, 5),
        ("tape_paper_500ft", "roll", 1.52, 0.05, 2),
        ("insulation_batt_3.5in", "sf", 1000, 0.05, 1050),
    ]


def test_every_line_has_the_contract_shape_and_a_price_book_key(mock_db):
    for condition, lf, height in ((A1, 100, 10), (A1, 37.5, 9), ({**A1, "corner_bead_lf": 24}, 12, 8)):
        for l in lines_for(condition, lf, height):
            assert set(l) == {"item", "qty", "unit", "computed_qty", "waste"}
            assert l["qty"] >= l["computed_qty"] >= 0 and 0 <= l["waste"] <= 1
            priced = mock_db.price_book.find_one({"item": l["item"]})
            assert priced, f"{l['item']} is not a price_book key"
            assert priced["unit"] == l["unit"]


def test_stock_lengths_follow_the_wall_height():
    for height, stud, board in ((8, 8, 8), (9, 9, 10), (10, 10, 10), (11, 12, 12), (12, 12, 12), (13, 14, 12), (20, 16, 12)):
        items = by_item(lines_for(A1, 100, height))
        assert f"stud_3.625_20ga_{stud}ft" in items and f"board_0.625_type_x_4x{board}" in items, height


def test_layers_and_sides():
    two_layers = {**A1, "layers_each_side": [{"thickness_in": 0.625, "board_type": "type_x"}] * 2}
    items = by_item(lines_for(two_layers, 100, 10))
    assert items["board_0.625_type_x_4x10"]["computed_qty"] == 100  # 100 x 10 x 2 x 2 / 40
    assert items["screws_1.25in"]["computed_qty"] == 4  # 4000 sf of board
    assert items["mud_all_purpose"]["computed_qty"] == pytest.approx(2000 / 450, abs=0.001)  # finished area doesn't double

    mixed = {**A1, "layers_each_side": [{"thickness_in": 0.625, "board_type": "type_x"}, {"thickness_in": 0.5, "board_type": "regular"}]}
    boards = {k: v["computed_qty"] for k, v in by_item(lines_for(mixed, 100, 10)).items() if k.startswith("board_")}
    assert boards == {"board_0.625_type_x_4x10": 50, "board_0.5_regular_4x10": 50}

    one_side = by_item(lines_for({**A1, "sides": 1}, 100, 10))
    assert one_side["board_0.625_type_x_4x10"]["computed_qty"] == 25


def test_insulation_only_when_the_condition_has_it_and_sized_to_the_stud():
    assert "insulation_batt_3.5in" not in by_item(lines_for({**A1, "insulation": None}, 100, 10))
    assert not any(l["item"].startswith("insulation") for l in lines_for({**A1, "insulation": "None"}, 100, 10))
    six = by_item(lines_for({**A1, "studs": {"size_in": 6, "gauge": 20, "spacing_in": 16}, "insulation": "R-19"}, 100, 10))
    assert "insulation_batt_6in" in six and "stud_6_20ga_10ft" in six and "track_6_20ga_10ft" in six


def test_corner_bead_only_when_passed_in():
    assert not any(l["item"].startswith("corner_bead") for l in lines_for(A1, 100, 10))
    bead = by_item(lines_for({**A1, "corner_bead_lf": 25}, 100, 10))["corner_bead_10ft"]
    assert (bead["computed_qty"], bead["qty"], bead["waste"]) == (3, 3, 0.0)


def test_stud_spacing_and_extra():
    items = by_item(lines_for({**A1, "studs": {"size_in": 3.625, "gauge": 20, "spacing_in": 24}}, 100, 10))
    assert items["stud_3.625_20ga_10ft"]["computed_qty"] == 51  # ceil(1200 / 24) + 1
    assert by_item(lines_for(A1, 0.5, 10))["stud_3.625_20ga_10ft"]["computed_qty"] == 2  # a short run still needs both ends


def test_qty_is_the_computed_qty_plus_waste_rounded_up_so_the_guard_never_trips():
    for lf in (1, 12.5, 37.5, 96, 100, 250.25, 1234):
        for height in (8, 9, 10, 12, 14):
            for l in lines_for(A1, lf, height):
                allowed = math.ceil(round(l["computed_qty"] * (1 + l["waste"]), 6))
                assert l["qty"] == allowed, (lf, height, l)


def test_constants_are_the_only_numbers_to_change(monkeypatch):
    monkeypatch.setitem(order_math.CONSTANTS["waste"], "stud", 0.0)
    monkeypatch.setitem(order_math.CONSTANTS, "track_length_ft", 20)
    items = by_item(lines_for(A1, 100, 10))
    assert items["stud_3.625_20ga_10ft"]["qty"] == 76
    assert items["track_3.625_20ga_20ft"]["computed_qty"] == 10


def test_a_condition_without_studs_or_layers_is_a_clear_error():
    with pytest.raises(ValueError, match="studs"):
        lines_for({"type_tag": "X"}, 100, 10)
    with pytest.raises(ValueError, match="layers"):
        lines_for({**A1, "layers_each_side": []}, 100, 10)
