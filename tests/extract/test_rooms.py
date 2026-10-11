"""Floor plans: a room number stacked with an uppercase name is a room, labelled at its centre."""
from extract.rooms import find_rooms
from extract.sheetinfo import PageText, common_title_block_text, locate_title_blocks
from scripts.make_drawing_set import H
from tests.extract.helpers import seg


def _room_rects(view: int) -> list[tuple[float, float, float, float]]:
    """(x0, top, x1, bottom) in PDF points, top-left origin, of the 3 rooms make_drawing_set draws per view."""
    x0, y0 = 120 + view * 1050, 360
    return [(x0 + 40 + r * 280, H - (y0 + 900), x0 + 280 + r * 280, H - (y0 + 600)) for r in range(3)]


def test_rooms_on_a101(sheet_by_no, synthetic_pages):
    locate_title_blocks(synthetic_pages, common_title_block_text(synthetic_pages))
    rooms = find_rooms(sheet_by_no["A-101"])
    assert [(r["number"], r["name"]) for r in rooms] == [("101", "OFFICE"), ("102", "CORRIDOR"), ("103", "STORAGE")]
    for room, (x0, top, x1, bottom) in zip(rooms, _room_rects(0)):
        x, y = room["label_pt"]
        assert x0 < x < x1 and top < y < bottom, (room, (x0, top, x1, bottom))


def test_duplicate_numbers_on_one_sheet_are_skipped(sheet_by_no, synthetic_pages):
    locate_title_blocks(synthetic_pages, common_title_block_text(synthetic_pages))
    rooms = find_rooms(sheet_by_no["A-102"])  # the enlarged plan repeats 101-103
    assert [r["number"] for r in rooms] == ["101", "102", "103"]


def test_room_label_shapes():
    p = PageText(
        2592,
        1728,
        [
            seg("MECH.", 300, 280),
            seg("ROOM", 302, 292),
            seg("204A", 302, 304),  # two-line name
            seg("1001", 800, 300),
            seg("CONFERENCE", 785, 312),  # number above the name
            seg("150", 1200, 300),  # door or keynote: nothing stacked with it
            seg("SEE 3/A-501", 1200, 312),
            seg("2610", 2350, 200),  # project number in the title block
            seg("LOBBY", 2340, 188),
        ],
        tb_corner=(2300, 0),
    )
    rooms = find_rooms(p)
    assert [(r["number"], r["name"]) for r in rooms] == [("204A", "MECH. ROOM"), ("1001", "CONFERENCE")]
    x, y = rooms[0]["label_pt"]
    assert 300 < x < 330 and 280 < y < 314
