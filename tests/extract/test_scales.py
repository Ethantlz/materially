"""View-title scales written in separate cells: a "SCALE" label cell and its value cell (the Ottawa set)."""
import pytest
from reportlab.pdfgen import canvas

from extract.ingest import read_pages, sheet_docs
from scripts.make_drawing_set import H, W, _title_block

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

