"""Sub-conditions on the takeoff screen: the views the panel is built from, and the shipped JS."""
from pathlib import Path

from ui.common import condition_views

ROOT = Path(__file__).resolve().parents[2]
STUDS = {"size_in": 3.625, "gauge": 20, "spacing_in": 16}
LAYERS = [{"thickness_in": 0.625, "board_type": "type_x"}]


def cond(cid, tag, **kw):
    return {"id": cid, "job_id": "j", "type_tag": tag, "studs": STUDS, "layers_each_side": LAYERS, "sides": 2, "color": "#" + cid * 6, **kw}


def test_condition_views_pass_parent_id_and_height():
    parent = cond("a", "A1", height_ft=10)
    sub = cond("b", "A1", parent_id="a", height_ft=12)
    views = {v["id"]: v for v in condition_views([parent, sub])}
    assert views["b"]["parent_id"] == "a" and views["b"]["height_ft"] == 12
    assert views["a"]["parent_id"] is None and views["a"]["height_ft"] == 10


def test_children_sit_right_after_their_parent():
    conds = [
        cond("5", "B1"),
        cond("9", "A1", parent_id="1", height_ft=14),
        cond("1", "A1", height_ft=10),
        cond("7", "A1", parent_id="1", height_ft=12),
        cond("3", "A2"),
        cond("2", "A2", parent_id="3", height_ft=9),
    ]
    views = condition_views(conds)
    assert [v["id"] for v in views] == ["1", "7", "9", "3", "2", "5"], "A1, its heights low to high, A2, its height, B1"
    assert [v["parent_id"] for v in views] == [None, "1", "1", None, "3", None]


def test_a_child_whose_parent_is_gone_is_still_listed():
    views = condition_views([cond("a", "A1"), cond("b", "C1", parent_id="gone", height_ft=12)])
    assert sorted(v["id"] for v in views) == ["a", "b"]


def test_children_without_a_colour_still_get_one():
    parent, sub = cond("a", "A1"), cond("b", "A1", parent_id="a", height_ft=12)
    del parent["color"], sub["color"]
    colours = [v["color"] for v in condition_views([parent, sub])]
    assert all(colours) and len(set(colours)) == 2


def test_the_panel_has_the_add_height_control_and_the_api_call():
    js = (ROOT / "ui" / "static" / "takeoff.js").read_text(encoding="utf-8")
    api = (ROOT / "ui" / "static" / "api.js").read_text(encoding="utf-8")
    css = (ROOT / "ui" / "static" / "takeoff.css").read_text(encoding="utf-8")
    for tid in ("cond-add-height", "cond-height-input", "cond-sub-row"):
        assert f'data-testid="{tid}"' in js, tid
    assert "Takeoff.addHeight(" in js
    assert "addHeight:" in api and "/subconditions" in api
    assert ".crow.sub" in css
