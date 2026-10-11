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
