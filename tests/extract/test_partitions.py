"""Partition sheets: find the type tags and group the text around each into one block."""
from extract.partitions import find_tags, partition_blocks
from extract.sheetinfo import PageText, common_title_block_text, locate_title_blocks
from tests.extract.helpers import seg

STUD_LINE = '3 5/8" 20 GA STUDS @ 16" O.C.'
LAYER_LINE = '(1) LAYER 5/8" TYPE X GYP. BD. EACH SIDE'


def test_synthetic_a501_tags(sheet_by_no, synthetic_pages):
    locate_title_blocks(synthetic_pages, common_title_block_text(synthetic_pages))
    assert [t.text for t in find_tags(sheet_by_no["A-501"])] == ["A1", "A2", "B1"]


def test_synthetic_a501_blocks_hold_their_own_lines(sheet_by_no, synthetic_pages):
    locate_title_blocks(synthetic_pages, common_title_block_text(synthetic_pages))
    blocks = partition_blocks(sheet_by_no["A-501"])
    assert [b.tag for b in blocks] == ["A1", "A2", "B1"]
    for b in blocks:
        assert b.text.splitlines()[0] == b.tag
        assert b.text.count(STUD_LINE) == 1, b.text
        assert b.text.count(LAYER_LINE) == 1, b.text
        x0, top, x1, bottom = b.bbox
        assert x0 <= b.tag_seg.x0 and top <= b.tag_seg.top and x1 > x0 and bottom > top


def test_title_block_and_plan_tokens_are_not_tags():
    p = PageText(
        2592,
        1728,
        [
            seg("A-501", 2350, 1620, 40),  # sheet number, in the title block
            seg("A1", 2350, 1500),  # in the title block
            seg("1", 400, 900, 12),  # view number
            seg("R-19", 400, 300),  # insulation R-value
            seg("101", 600, 600),  # room number
            seg("TYPE X", 600, 700),
            seg("P-3", 1000, 300, 12),
            seg("3B", 1400, 300, 12),
            seg("TYPE A4", 1800, 300, 12),
        ],
        tb_corner=(2300, 0),
    )
    assert [t.text for t in find_tags(p)] == ["P-3", "3B", "TYPE A4"]
    assert [b.tag for b in partition_blocks(p)] == []  # no stud or board text near any of them


def _cell(tag: str, x: float, y: float) -> list:
    """A wall-type cell with the tag at its top left and notes that run most of the cell's width."""
    return [
        seg(tag, x + 20, y + 20, 14),
        seg(f"{STUD_LINE} ({tag})", x + 20, y + 70),
        seg(LAYER_LINE, x + 20, y + 84),
        seg(f"TO DECK ({tag})", x + 400, y + 98),  # past the midpoint between this tag and the next one
        seg("UL U419   1 HR   STC 50", x + 20, y + 300),
    ]


def test_grid_cells_keep_their_own_text():
    segs = [*_cell("A1", 100, 100), *_cell("A2", 700, 100), *_cell("B1", 100, 500), *_cell("B2", 700, 500)]
    segs += [seg("GENERAL PARTITION NOTES", 1400, 120, 12), seg("1. ALL STUDS 20 GA UNLESS NOTED", 1400, 140)]
    p = PageText(2592, 1728, segs, tb_corner=(2300, 0))
    blocks = {b.tag: b for b in partition_blocks(p)}
    assert sorted(blocks) == ["A1", "A2", "B1", "B2"]
    for tag, b in blocks.items():
        assert f"STUDS @ 16\" O.C. ({tag})" in b.text and b.text.count("STUDS @") == 1, b.text
        assert b.text.splitlines()[-2] == f"TO DECK ({tag})" and "U419" in b.text.splitlines()[-1], b.text
        assert "GENERAL" not in b.text


def test_schedule_rows_keep_their_own_text():
    segs = [seg("TYPE", 100, 170), seg("STUDS", 200, 170), seg("BOARD", 480, 170)]
    for i, (tag, gauge) in enumerate([("A1", "20 GA"), ("A2", "25 GA"), ("A3", "18 GA")]):
        y = 200 + 40 * i
        segs += [seg(tag, 100, y), seg(f'3 5/8" {gauge} @ 16" O.C.', 200, y), seg('(1) 5/8" TYPE X EA. SIDE', 480, y)]
    p = PageText(2592, 1728, segs, tb_corner=(2300, 0))
    blocks = partition_blocks(p)
    assert [b.tag for b in blocks] == ["A1", "A2", "A3"]
    assert [b.text.count(" GA @") for b in blocks] == [1, 1, 1]
    assert "20 GA" in blocks[0].text and "25 GA" in blocks[1].text and "18 GA" in blocks[2].text


def _schedule_sheet() -> PageText:
    """Two wall families, each a section with callouts over a schedule whose columns are the wall types
    (the WCU A003 layout), plus a tag legend in the notes column."""
    segs = [
        seg("SA", 200, 60, 23),
        seg("5/8\" TYPE 'X' GYP. BD.", 500, 250),
        seg('METAL STUD FRAMING @ 16" OC', 500, 280),
        seg("25 GA. RESILIENT CHANNEL", 220, 300),
        seg("SA4", 420, 470), seg("SA6", 492, 470), seg("-", 564, 470), seg("-", 636, 470),
        seg("STUD SIZE", 200, 488), seg('3 5/8"', 420, 488), seg('6"', 495, 488),
        seg("ACTUAL DIMENSION 'X'", 200, 508), seg('5 3/8"', 420, 507), seg('7 3/4"', 490, 507),
        seg("FIRE RATING", 200, 530), seg("1 HR", 422, 524), seg("UL# U419", 414, 535), seg("-", 494, 524),
        seg("SB", 760, 60, 23),
        seg("5/8\" TYPE 'X' GYP. BD. (SB)", 1100, 250),
        seg('METAL STUD FRAMING @ 24" OC', 1100, 280),
        seg("SB2", 980, 470), seg("-", 1052, 470),
        seg("STUD SIZE", 760, 488), seg("SEE UL DET.", 965, 488),
        seg("FIRE RATING", 760, 530), seg("2 HR", 980, 524),
        seg("GENERAL NOTES", 1400, 60, 23),
        seg("SA4", 1500, 400), seg("S - STEEL STUD", 1400, 410),
    ]
    return PageText(3024, 2160, segs, tb_corner=(2700, 0))


def test_schedule_columns_become_blocks():
    blocks = {b.tag: b for b in partition_blocks(_schedule_sheet())}
    assert sorted(blocks) == ["SA4", "SA6", "SB2"], "one block per schedule column; the legend SA4 is not one"
    sa4, sa6, sb2 = blocks["SA4"].text, blocks["SA6"].text, blocks["SB2"].text
    assert sa4.splitlines()[0] == "SA4"
    assert 'STUD SIZE: 3 5/8"' in sa4 and "ACTUAL DIMENSION 'X': 5 3/8\"" in sa4 and "FIRE RATING: 1 HR UL# U419" in sa4
    assert '7 3/4"' not in sa4 and 'STUD SIZE: 6"' in sa6 and "ACTUAL DIMENSION 'X': 7 3/4\"" in sa6
    assert all("RESILIENT CHANNEL" in t and '16" OC' in t and "(SB)" not in t for t in (sa4, sa6))
    assert "STUD SIZE: SEE UL DET." in sb2 and "FIRE RATING: 2 HR" in sb2 and '24" OC' in sb2 and "RESILIENT" not in sb2
    assert not any("GENERAL NOTES" in t or "STEEL STUD" in t for t in (sa4, sa6, sb2))


def _list_sheet() -> PageText:
    """An assembly schedule written as a list (the Ottawa A001 layout): tags down one column, some on their own,
    some starting their line, each with a heading beside the tag and indented lines under it; a site note sits
    to the right in the same rows."""
    s = 11.1
    segs = [
        seg("P1 PARTITION TYPE NOT USED", 1098, 397, 10.7),
        seg("INTERIOR PARTITION WALL CONSTRUCTION", 1131, 443.5, s), seg("P2", 1098, 449.6, 10.7),
        seg("FLOOR TO U/S OF SLAB", 1131, 457.9, s),
        seg("-16 mm TYPE X GYPSUM BOARD BOTH SIDES", 1158, 472.2, s),
        seg("-92 mm STEEL STUDS @ MAX. 400mm O.C.", 1158, 486.5, s),
        seg("INTERIOR PARTITION WALL CONSTRUCTION - MIN. STC 46", 1131, 537.5, s), seg("P3", 1098, 542.4, 10.7),
        seg("-16 mm TYPE X GYPSUM BOARD BOTH SIDES", 1158, 552, s),
        seg("-92 mm STEEL STUDS @ 600mm O.C. FILLED WITH", 1158, 566.2, s),
        seg("CREATE FENCED STAGING AREA IN", 1624, 573.8, 9.3), seg("EXISTING PARKING AREA", 1624, 585.7, 9.3),
        seg("-MINERAL FIBER INSULATION", 1158, 580.5, s),
        seg("P5 INTERIOR SECURE DEMISING WALL - 1HR", 1098, 861, 10.7),
        seg("ULC W407:", 1132, 874, 12.3),
        *[seg(f"-16mm TYPE X GYPSUM BOARD LINE {i}", 1166, 888.7 + 13.4 * i, 10.2) for i in range(10)],
        seg("N", 1645, 1162, 9.3),
    ]
    return PageText(2835, 2004, segs, tb_corner=(2282, 0))


def test_list_layout_blocks():
    blocks = {b.tag: b.text for b in partition_blocks(_list_sheet())}
    assert sorted(blocks) == ["P2", "P3", "P5"], "P1 says 'not used': no stud or board wording"
    assert blocks["P2"].splitlines()[1] == "INTERIOR PARTITION WALL CONSTRUCTION", "the heading beside the tag starts the block"
    assert "400mm" in blocks["P2"] and "600mm" not in blocks["P2"]
    assert "MINERAL FIBER" in blocks["P3"] and "STAGING" not in blocks["P3"] and "PARKING" not in blocks["P3"]
    assert blocks["P5"].splitlines()[1] == "INTERIOR SECURE DEMISING WALL - 1HR", "a tag that starts its line is split off"
    assert "LINE 9" in blocks["P5"] and "\nN" not in blocks["P5"]


def test_a_line_starting_with_a_tag_alone_is_not_a_tag():
    p = PageText(2592, 1728, [seg("A1 MOUNTED BOARD BY OWNER", 400, 400), seg('3 5/8" STUDS', 400, 412)], tb_corner=(2300, 0))
    assert find_tags(p) == [] and partition_blocks(p) == []
