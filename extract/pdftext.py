"""Fast text segments from a PDF page via pdfium's character API.

pdfplumber parses every vector path on the page, which takes seconds on dense CAD sheets; pdfium's
text page is milliseconds. A segment is a run of upright characters on one baseline with one size and
no large gap, so "SCALE:" and its value in separate title-block cells come out as separate segments.
Coordinates are PDF points, origin top-left, y down.
"""
import math
from dataclasses import dataclass, field

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c


@dataclass
class Seg:
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    size: float  # loose glyph box height, roughly 1.15x the font size
    _spaces: int = field(default=0, repr=False)

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2

    def gap_to(self, other: "Seg") -> float:
        dx = max(0.0, other.x0 - self.x1, self.x0 - other.x1)
        dy = max(0.0, other.top - self.bottom, self.top - other.bottom)
        return math.hypot(dx, dy)


def _upright(angle: float) -> bool:
    return angle < 0 or min(angle, 2 * math.pi - angle) < 0.1  # -1 = unknown, treat as upright


def page_segments(page: pdfium.PdfPage) -> list[Seg]:
    _, height = page.get_size()
    tp = page.get_textpage()
    try:
        raw = tp.raw
        segs: list[Seg] = []
        cur: Seg | None = None
        pending_space = False
        for i in range(tp.count_chars()):
            code = pdfium_c.FPDFText_GetUnicode(raw, i)
            if code in (0, 0xFFFE, 0xFFFF):
                continue
            ch = chr(code)
            if ch in "\r\n":
                cur = _flush(segs, cur)
                pending_space = False
                continue
            if ch.isspace():
                pending_space = True
                continue
            if not _upright(pdfium_c.FPDFText_GetCharAngle(raw, i)):
                continue
            left, bottom, right, top = tp.get_charbox(i, loose=True)
            size = top - bottom
            if size <= 0:
                continue
            y_top, y_bottom = height - top, height - bottom
            if cur is not None:
                gap = left - cur.x1
                same_line = abs(y_bottom - cur.bottom) < 0.35 * size and abs(size - cur.size) < 0.25 * max(size, cur.size)
                max_gap = (3.0 if pending_space else 1.0) * size
                if same_line and -0.3 * size < gap < max_gap:
                    if pending_space or gap > 0.25 * size:
                        cur.text += " "
                    cur.text += ch
                    cur.x1 = max(cur.x1, right)
                    cur.top, cur.bottom = min(cur.top, y_top), max(cur.bottom, y_bottom)
                    pending_space = False
                    continue
                cur = _flush(segs, cur)
            cur = Seg(ch, left, y_top, right, y_bottom, size)
            pending_space = False
        _flush(segs, cur)
        return segs
    finally:
        tp.close()


def _flush(segs: list[Seg], cur: Seg | None) -> None:
    if cur is not None and cur.text.strip():
        cur.text = cur.text.strip()
        segs.append(cur)
    return None


def bookmarks_by_page(pdf: pdfium.PdfDocument) -> dict[int, str]:
    """0-based page index -> bookmark title (deepest bookmark wins, since sets nest by discipline)."""
    out: dict[int, tuple[int, str]] = {}
    try:
        for item in pdf.get_toc():
            idx = item.page_index
            title = (item.title or "").strip()
            if idx is None or not title:
                continue
            if idx not in out or item.level >= out[idx][0]:
                out[idx] = (item.level, title)
    except Exception:  # malformed outlines are common; bookmarks are a bonus, not required
        return {}
    return {k: v for k, (_, v) in out.items()}


def page_label(pdf: pdfium.PdfDocument, index: int) -> str:
    try:
        return (pdf.get_page_label(index) or "").strip()
    except Exception:
        return ""
