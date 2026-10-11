"""Title-block locator: right strip vs bottom band, from text that repeats across the set."""
from extract.ingest import sheet_docs
from extract.pdftext import Seg
from extract.sheetinfo import PageText, common_title_block_text, locate_title_blocks, pick_scale
from scripts.make_drawing_set import EXPECTED, TB_X0

W, H = 2592.0, 1728.0


def test_synthetic_set_is_a_right_strip(synthetic_pages):
    common = common_title_block_text(synthetic_pages)
    locate_title_blocks(synthetic_pages, common)
    for p in synthetic_pages:
        assert p.tb_corner is not None
        x, y = p.tb_corner
        assert y == 0, "a right strip runs the full height"
        assert 0.72 * W < x <= TB_X0 + 16, x


def test_view_titles_along_the_bottom_are_not_title_block(sheet_by_no, synthetic_pages):
    locate_title_blocks(synthetic_pages, common_title_block_text(synthetic_pages))
    a102 = sheet_by_no["A-102"]
    view_scales = [s for s in a102.segs if s.text.startswith("SCALE:")]
    assert len(view_scales) == 2
    assert not any(a102.in_title_block(s) for s in view_scales)
    assert pick_scale(a102)["source"] == "view_title"


def test_scale_source_matches_expected_on_every_sheet(synthetic_pages):
    docs = sheet_docs("job", [("synthetic.pdf", synthetic_pages)])
    assert [(d["sheet_no"], d["scale"]["source"]) for d in docs] == [(e["sheet_no"], e["scale_source"]) for e in EXPECTED]


def _seg(text: str, x0: float, top: float, size: float = 10.0) -> Seg:
    return Seg(text, x0, top, x0 + 0.6 * size * len(text), top + size, size)


def _bottom_band_page(sheet_no: str, title: str) -> PageText:
    """A set whose title block runs along the bottom edge, with a section cut on the right of the drawing."""
    band = [
        _seg("SAMPLE ARCHITECTS", 80, 1580, 16),
        _seg("100 MAIN STREET", 80, 1600),
        _seg("MATERIALLY TEST BUILDING", 700, 1580, 14),
        _seg("PROJECT NO.", 1300, 1580, 8),
        _seg("2610", 1400, 1580),
        _seg("SHEET TITLE", 1700, 1570, 8),
        _seg(title, 1700, 1590, 18),
        _seg("SCALE", 1700, 1630, 8),
        _seg("AS NOTED", 1700, 1642),
        _seg("SHEET NUMBER", 2300, 1570, 8),
        _seg(sheet_no, 2300, 1590, 40),
    ]
    drawing = [
        _seg("WALL SECTION", 2000, 1290, 14),
        _seg("SCALE: 3/4\" = 1'-0\"", 2000, 1308, 9),
    ]
    return PageText(W, H, drawing + band)


def test_bottom_band_title_block():
    pages = [_bottom_band_page(f"A-30{i}", f"WALL SECTIONS {i}") for i in range(1, 5)]
    locate_title_blocks(pages, common_title_block_text(pages))
    for p in pages:
        x, y = p.tb_corner
        assert x == 0, "a bottom band runs the full width"
        assert 1290 + 18 < y <= 1570, y
        section_scale = next(s for s in p.segs if s.text.startswith("SCALE:"))
        assert not p.in_title_block(section_scale)
        assert pick_scale(p)["source"] == "view_title"


def test_pages_without_repeating_text_keep_the_edge_band():
    pages = [_bottom_band_page("A-301", "WALL SECTIONS")]
    locate_title_blocks(pages, common_title_block_text(pages))  # fewer than 3 sheets: nothing repeats
    assert pages[0].tb_corner is None
