"""View-title scales written in separate cells: a "SCALE" label cell and its value cell (the Ottawa set)."""
import pytest
from reportlab.pdfgen import canvas

from app.settings import ROOT
from extract.ingest import read_pages, sheet_docs
from extract.sheetinfo import common_title_block_text, locate_title_blocks, pick_scale
from scripts.make_drawing_set import H, W, _title_block

OTTAWA = ROOT.parent / "materially" / "fixtures" / "ottawa-arch-tender.pdf"


def _view(c: canvas.Canvas, x: float, y: float, title: str, value: str, below: bool = False) -> None:
    """A view title over a label cell "SCALE" and a value cell on one baseline, 52 pt apart (far enough that
    pdfium keeps them as two segments, as in the Ottawa set), or the value cell under the label."""
    c.setFont("Helvetica-Bold", 16)
    c.drawString(x, y, title)
    c.setFont("Helvetica", 6.5)
    c.drawString(x, y - 20, "SCALE")
    if below:
        c.drawString(x, y - 29, value)
    else:
        c.drawString(x + 52, y - 20, value)


@pytest.fixture(scope="module")
def split_cells(tmp_path_factory):
    path = tmp_path_factory.mktemp("cells") / "cells.pdf"
    c = canvas.Canvas(str(path), pagesize=(W, H))
    pages = [
        ("A-111", ["GROUND FLOOR PLAN"], [("GROUND FLOOR PLAN", "1:100"), ("SECOND FLOOR PLAN", "1:100")], False),
        ("A-201", ["INTERIOR ELEVATIONS"], [("ELEVATION 1", "1:50")], True),
        ("A-301", ["NOTES"], [], False),
    ]
    for no, title, views, below in pages:
        for i, (name, value) in enumerate(views):
            _view(c, 200 + 900 * i, 400, name, value, below)
        if no == "A-301":  # ratios with no SCALE label next to them
            c.setFont("Helvetica", 10)
            c.drawString(200, 900, "SLOPE 1:12 MAX AT RAMPS")
            c.drawString(200, 940, "1:12")
            c.drawString(260, 940, "MEETING 10:30")
        _title_block(c, no, title, "AS NOTED")
        c.showPage()
    c.save()
    return path


def _scales(path):
    return [d["scale"] for d in sheet_docs("job", [(path.name, read_pages(path))])]


def test_scale_label_and_ratio_in_separate_cells(split_cells):
    pages = read_pages(split_cells)
    labels = [s.text for s in pages[0].segs if s.text in ("SCALE", "1:100")]
    assert labels.count("SCALE") == 3 and labels.count("1:100") == 2, "two view cells apart, plus the title block label"
    plan = _scales(split_cells)[0]
    assert (plan["label"], plan["source"], plan["pt_per_ft"]) == ("1:100", "view_title", 8.64)


def test_value_cell_under_the_label(split_cells):
    assert _scales(split_cells)[1]["label"] == "1:50"


def test_a_bare_ratio_needs_a_scale_label(split_cells):
    assert _scales(split_cells)[2] is None


@pytest.mark.skipif(not OTTAWA.exists(), reason=f"{OTTAWA} not on disk")
def test_ottawa_names_and_scales():
    docs = {d["sheet_no"]: d for d in sheet_docs("job", [(OTTAWA.name, read_pages(OTTAWA))])}
    assert {no: d["title"] for no, d in docs.items()} == {
        "A001": "COVER PAGE, DRAWING LIST, ASSEMBLY SCHEDULE",
        "A111": "DEMOLITION PLANS",
        "A112": "REFLECTED CEILING PLANS DEMOLITION",
        "A121": "CONSTRUCTION/ FINISH PLANS",
        "A131": "REFLECTED CEILING PLANS NEW",
        "A151": "EQUIPMENT/ FURNITURE PLANS",
        "A201": "INTERIOR ELEVATIONS",
        "A202": "INTERIOR ELEVATIONS",
        "A501": "DETAILS",
        "A601": "DOOR ELEVATIONS AND SCHEDULE",
    }
    labels = {no: (d["scale"] or {}).get("label") for no, d in docs.items()}
    assert {no: labels[no] for no in ("A111", "A121", "A201", "A202", "A501")} == {
        "A111": "1:100", "A121": "1:100", "A201": "1:50", "A202": "1:50", "A501": "1:5",
    }
    # A001's only view is the STAGING AREA LOCATION PLAN, drawn at 1:500
    assert labels["A001"] == "1:500" and docs["A001"]["scale"]["source"] == "view_title"
    assert all(docs[no]["scale"]["source"] == "view_title" for no in ("A111", "A121", "A201", "A202", "A501"))
