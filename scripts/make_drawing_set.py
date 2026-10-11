"""Generate a synthetic ARCH D drawing set with a realistic right-strip title block.

    uv run python -m scripts.make_drawing_set fixtures/synthetic.pdf [--bookmarks]

EXPECTED lists what extraction should find on each page.
"""
import sys
from pathlib import Path

from reportlab.lib.pagesizes import landscape
from reportlab.pdfgen import canvas

W, H = landscape((24 * 72, 36 * 72))  # ARCH D, 36" x 24"
M = 36
TB_X0 = W - 300

ARCH_8 = "1/8\" = 1'-0\""
ARCH_4 = "1/4\" = 1'-0\""

# sheet_no, title lines, title-block scale, view titles [(name, scale)]
SHEETS = [
    ("G-001", ["COVER SHEET"], "NTS", []),
    ("A-101", ["FIRST FLOOR PLAN"], ARCH_8, [("FIRST FLOOR PLAN", ARCH_8)]),
    ("A-102", ["SECOND FLOOR PLAN"], "AS NOTED", [("SECOND FLOOR PLAN", ARCH_4), ("ENLARGED TOILET PLAN", ARCH_4)]),
    ("A-201", ["EXTERIOR", "ELEVATIONS"], "3/16\" = 1'-0\"", [("NORTH ELEVATION", "3/16\" = 1'-0\"")]),
    ("A-401", ["REFLECTED CEILING PLAN"], ARCH_8, [("FIRST FLOOR RCP", ARCH_8)]),
    ("A-501", ["PARTITION TYPES"], "1 1/2\" = 1'-0\"", []),
    ("C-101", ["SITE PLAN"], "1\" = 20'", [("SITE PLAN", "1\" = 20'")]),
]

EXPECTED = [
    {"sheet_no": "G-001", "title": "COVER SHEET", "kind": "other", "scale": "NTS", "scale_source": "title_block"},
    {"sheet_no": "A-101", "title": "FIRST FLOOR PLAN", "kind": "floor_plan", "scale": ARCH_8, "scale_source": "title_block"},
    {"sheet_no": "A-102", "title": "SECOND FLOOR PLAN", "kind": "floor_plan", "scale": ARCH_4, "scale_source": "view_title"},
    {"sheet_no": "A-201", "title": "EXTERIOR ELEVATIONS", "kind": "elevations", "scale": "3/16\" = 1'-0\"", "scale_source": "title_block"},
    {"sheet_no": "A-401", "title": "REFLECTED CEILING PLAN", "kind": "reflected_ceiling", "scale": ARCH_8, "scale_source": "title_block"},
    {"sheet_no": "A-501", "title": "PARTITION TYPES", "kind": "partition_types", "scale": "1 1/2\" = 1'-0\"", "scale_source": "title_block"},
    {"sheet_no": "C-101", "title": "SITE PLAN", "kind": "other", "scale": "1\" = 20'", "scale_source": "title_block"},
]


def _title_block(c: canvas.Canvas, sheet_no: str, title: list[str], scale: str) -> None:
    c.rect(TB_X0, M, W - M - TB_X0, H - 2 * M)
    x = TB_X0 + 16
    y = H - M - 30
    c.setFont("Helvetica-Bold", 14)
    c.drawString(x, y, "SAMPLE ARCHITECTS")
    c.setFont("Helvetica", 8)
    for line in ["100 MAIN STREET", "STATE COLLEGE, PA 16801", "(814) 555-0100"]:
        y -= 12
        c.drawString(x, y, line)
    y -= 60
    c.setFont("Helvetica-Bold", 13)
    c.drawString(x, y, "MATERIALLY TEST BUILDING")
    c.setFont("Helvetica", 8)
    y -= 12
    c.drawString(x, y, "200 COLLEGE AVENUE")
    for label, value in [("PROJECT NO.", "2610"), ("DATE", "10/11/2026"), ("DRAWN BY", "EL")]:
        y -= 40
        c.setFont("Helvetica", 7)
        c.drawString(x, y, label)
        c.setFont("Helvetica", 10)
        c.drawString(x + 110, y, value)
    y = 330
    c.setFont("Helvetica", 7)
    c.drawString(x, y, "SCALE")
    c.setFont("Helvetica", 10)
    c.drawString(x, y - 14, scale)
    y = 270
    c.setFont("Helvetica", 7)
    c.drawString(x, y, "SHEET TITLE")
    c.setFont("Helvetica-Bold", 18)
    for i, line in enumerate(title):
        c.drawString(x, y - 24 - i * 22, line)
    c.setFont("Helvetica", 7)
    c.drawString(x, 130, "SHEET NUMBER")
    c.setFont("Helvetica-Bold", 40)
    c.drawString(x, 75, sheet_no)


def _drawing(c: canvas.Canvas, sheet_no: str, views: list[tuple[str, str]]) -> None:
    c.rect(M, M, TB_X0 - 2 * M, H - 2 * M)
    if sheet_no == "G-001":
        c.setFont("Helvetica-Bold", 48)
        c.drawString(200, H - 300, "MATERIALLY TEST BUILDING")
        c.setFont("Helvetica-Bold", 14)
        c.drawString(200, H - 420, "DRAWING INDEX")
        c.setFont("Helvetica", 12)
        for i, (no, title, _, _) in enumerate(SHEETS):
            c.drawString(200, H - 450 - i * 18, f"{no}   {' '.join(title)}")
        return
    if sheet_no == "A-501":
        c.setFont("Helvetica", 10)
        for i, tag in enumerate(["A1", "A2", "B1"]):
            c.circle(200 + i * 500, H - 300, 18)
            c.drawCentredString(200 + i * 500, H - 304, tag)
            c.drawString(160 + i * 500, H - 360, "3 5/8\" 20 GA STUDS @ 16\" O.C.")
            c.drawString(160 + i * 500, H - 374, "(1) LAYER 5/8\" TYPE X GYP. BD. EACH SIDE")
        return
    for i, (name, scale) in enumerate(views):
        x0, y0 = 120 + i * 1050, 360
        c.rect(x0, y0, 900, 1000)
        c.setFont("Helvetica", 9)
        for r, (room, num) in enumerate([("OFFICE", "101"), ("CORRIDOR", "102"), ("STORAGE", "103")]):
            c.rect(x0 + 40 + r * 280, y0 + 600, 240, 300)
            c.drawCentredString(x0 + 160 + r * 280, y0 + 760, room)
            c.drawCentredString(x0 + 160 + r * 280, y0 + 748, num)
        c.drawString(x0 + 40, y0 + 200, "SEE 3/A-501 FOR PARTITION TYPES")
        c.circle(x0 + 20, y0 - 60, 16)
        c.setFont("Helvetica-Bold", 12)
        c.drawCentredString(x0 + 20, y0 - 64, str(i + 1))
        c.setFont("Helvetica-Bold", 14)
        c.drawString(x0 + 50, y0 - 58, name)
        c.setFont("Helvetica", 9)
        c.drawString(x0 + 50, y0 - 74, f"SCALE: {scale}")


def build(path: Path, bookmarks: bool = False) -> list[dict]:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=(W, H))
    for i, (no, title, scale, views) in enumerate(SHEETS):
        _drawing(c, no, views)
        _title_block(c, no, title, scale)
        if bookmarks:
            key = f"p{i}"
            c.bookmarkPage(key)
            c.addOutlineEntry(f"{no} - {' '.join(title)}", key, level=0)
        c.showPage()
    c.save()
    return EXPECTED


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "fixtures/synthetic.pdf")
    build(out, bookmarks="--bookmarks" in sys.argv)
    print(out)
