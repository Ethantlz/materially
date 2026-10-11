import pytest

from contracts.validate import errors_for
from contracts.schemas import MATERIAL
from extract.materials import derive

SA4_TEXT = """SA4
SLAB OR DECKING ABOVE,
DIAGONALLY BRACE BACK TO STRUCT @ 48" MAX
CONT FIRE RATED ACOUSTICAL SEALANT AT TOP & BOTTOM OF WALL (BOTH SIDES) FOR RATED WALLS.
(FULL CAVITY WIDTH) ACOUSTICAL BATT INSULATION, W/ GALV STEEL PINS
5/8" TYPE 'X' GYP. BD. PER UL
#" METAL STUD FRAMING @ 16" OC REF CHART BELOW.
STEEL RUNNER, FASTENED TO CONCRETE SLAB @ 12" OC TOP & BOTTOM OF METAL STUD FRAMING
25 GA. SINGLE LEG RESILIENT CHANNEL @ 16" OC TO ONE SIDE OF METAL STUDS.
STUD SIZE 3 5/8"
FIRE RATING 1 HR UL# U419
ACOUSTICAL RATING (W/ BATTS) STC-52"""

SA4 = {"type_tag": "SA4", "studs": {"size_in": 3.625, "gauge": None, "spacing_in": 16},
       "layers_each_side": [{"thickness_in": 0.625, "board_type": "type_x"}], "sides": 2,
       "insulation": "acoustical batt", "fire_rating": 1, "ul_assembly": "U419", "stc": 52}


def test_sa4_lists_every_material_on_the_detail():
    ms = derive(SA4, SA4_TEXT)
    by = {m["role"]: m for m in ms}
    assert set(by) == {"stud", "track", "board", "insulation", "resilient_channel", "sealant", "bracing"}
    assert by["stud"]["size_in"] == 3.625 and by["stud"]["gauge"] is None and by["stud"]["spacing_in"] == 16
    assert by["board"]["board_type"] == "type_x" and by["board"]["sides"] == 2
    rc = by["resilient_channel"]
    assert (rc["gauge"], rc["spacing_in"], rc["sides"]) == (25, 16, 1)
    assert by["sealant"]["key"] == "sealant_firerated"  # a rated wall gets the fire-rated sealant
    assert len({m["key"] for m in ms}) == len(ms)
    for m in ms:
        assert errors_for(MATERIAL, m) == [], m


def test_plain_condition_without_text_gets_the_core_materials():
    a1 = {"type_tag": "A1", "studs": {"size_in": 3.625, "gauge": 20, "spacing_in": 16},
          "layers_each_side": [{"thickness_in": 0.625, "board_type": "type_x"}, {"thickness_in": 0.625, "board_type": "type_x"}],
          "sides": 1, "insulation": None}
    ms = derive(a1)
    assert [m["role"] for m in ms] == ["stud", "track", "board"]
    assert "2 layers one side" in ms[2]["desc"]


def test_unrated_wall_gets_acoustical_sealant():
    ms = derive({**SA4, "fire_rating": None}, "FILL GAPS W/ ACOUSTICAL SEALANT")
    assert any(m["key"] == "sealant_acoustical" for m in ms)


def test_resilient_channel_gauge_outside_the_contract_stays_in_the_description():
    """A 24 ga RC callout has no slot in MATERIAL's GAUGES; the row still writes, with the number in desc."""
    ms = derive(SA4, '24 GA. RESILIENT CHANNEL @ 16" OC BOTH SIDES')
    rc = next(m for m in ms if m["role"] == "resilient_channel")
    assert rc["gauge"] is None and "24 ga" in rc["desc"]
    assert all(errors_for(MATERIAL, m) == [] for m in ms), ms


@pytest.mark.parametrize("condition", [
    {},
    {"studs": None},
    {"studs": {"size_in": 3.625, "gauge": 20, "spacing_in": 16}},
    {"studs": {"size_in": 3.625, "gauge": None, "spacing_in": 16},
     "layers_each_side": [{"thickness_in": 0.625, "board_type": "type_x"}]},  # no "sides"
    {"type_tag": "SA4", "studs": {"size_in": None, "gauge": None, "spacing_in": None}, "layers_each_side": None},
])
def test_partial_conditions_never_raise_and_only_yield_valid_materials(condition):
    ms = derive(condition)
    assert len({m["key"] for m in ms}) == len(ms)
    for m in ms:
        assert errors_for(MATERIAL, m) == [], m
