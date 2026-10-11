"""Partition-types sheets: find the wall type tags and group the text around each one into a block.

A tag is a short standalone token like A1, P-3, 1A or 3B (optionally written "TYPE A1"). Tags on a sheet are
laid out in columns (a grid of wall-type cells, or one column down a schedule). Column boundaries sit in the
widest whitespace gutter between neighbouring tag columns. Each other segment goes to the nearest tag at or
above it in its column, within one row pitch, so a cell's notes stay with its own tag. Blocks with no
stud/board wording (detail bubbles, grid labels) are dropped.
"""
import re
import statistics
from dataclasses import dataclass

from extract.pdftext import Seg
from extract.scale import normalize
from extract.sheetinfo import PageText

TAG = re.compile(r"(?:(?:WALL|PARTITION)\s+)?(?:TYPE\s+)?(?P<tag>[A-Z]{1,2}-?\d{1,2}[A-Z]?|\d{1,2}[A-Z])")
NOT_TAG = re.compile(r"R-\d{2}")  # insulation R-values (R-19)
PARTITION_WORDS = re.compile(
    r"STUD|\bGA\b|GAUGE|\bMIL\b|GYP|\bGWB\b|BOARD|\bBD\b|LAYER|O\.?\s?C\b|\bMTL\b|METAL|FRAMING|SHAFT|@"
)


@dataclass
class Block:
    tag: str
    tag_seg: Seg
    segs: list[Seg]  # the text grouped with the tag, tag excluded

    @property
    def text(self) -> str:
        return "\n".join([self.tag, *(" ".join(s.text for s in line) for line in _lines(self.segs))])

    @property
    def bbox(self) -> list[float]:
        every = [self.tag_seg, *self.segs]
        return [round(v, 1) for v in (min(s.x0 for s in every), min(s.top for s in every),
                                      max(s.x1 for s in every), max(s.bottom for s in every))]


def tag_of(s: Seg) -> str | None:
    t = normalize(s.text).strip()
    m = TAG.fullmatch(t)
    if not m or NOT_TAG.fullmatch(t):
        return None
    return m["tag"]


def find_tags(p: PageText) -> list[Seg]:
    """Tag segments outside the title block, in reading order."""
    return sorted((s for s in p.segs if not p.in_title_block(s) and tag_of(s)), key=lambda s: (s.top, s.x0))


def partition_blocks(p: PageText) -> list[Block]:
    tags = find_tags(p)
    if not tags:
        return []
    tag_ids = {id(t) for t in tags}
    text = [s for s in p.segs if id(s) not in tag_ids and not p.in_title_block(s)]
    tol = 0.5 * max(t.size for t in tags)

    columns = _columns(tags, tol)
    pitch_x = _median_step([statistics.fmean(t.cx for t in col) for col in columns])
    pitch_y = _median_step([t.top for col in columns for t in col], per=columns)
    reach_y = pitch_y or pitch_x or p.height  # how far below its tag a cell's text can sit

    grid = [s for s in text if min(t.top for t in tags) - tol <= s.top < max(t.top for t in tags) + reach_y]
    right_edge = p.tb_corner[0] if p.tb_corner and p.tb_corner[1] == 0 else p.width
    bounds = _column_bounds(columns, grid, pitch_x, right_edge)

    owned: dict[int, list[Seg]] = {id(t): [] for t in tags}
    for s in text:
        col = next((c for c, (lo, hi) in zip(columns, bounds) if lo <= s.cx < hi), None)
        above = [t for t in col or [] if t.top - tol <= s.top]
        if not above:
            continue
        t = max(above, key=lambda t: t.top)
        if s.top - t.top < reach_y:
            owned[id(t)].append(s)

    best: dict[str, Block] = {}
    for t in tags:
        b = Block(tag_of(t), t, owned[id(t)])
        if not PARTITION_WORDS.search(normalize(b.text)):
            continue
        if b.tag not in best or len(b.text) > len(best[b.tag].text):  # a tag drawn twice: keep the fuller block
            best[b.tag] = b
    return sorted(best.values(), key=lambda b: (b.tag_seg.top, b.tag_seg.x0))


def _columns(tags: list[Seg], tol: float) -> list[list[Seg]]:
    """Tags grouped by centre x, left to right."""
    cols: list[list[Seg]] = []
    for t in sorted(tags, key=lambda t: t.cx):
        if cols and t.cx - cols[-1][-1].cx <= max(6 * tol, 30):
            cols[-1].append(t)
        else:
            cols.append([t])
    return [sorted(c, key=lambda t: t.top) for c in cols]


def _median_step(values: list[float], per: list[list[Seg]] | None = None) -> float | None:
    """Median gap between consecutive values (within each column when `per` is given)."""
    if per is not None:
        steps = [b.top - a.top for col in per for a, b in zip(col, col[1:])]
    else:
        steps = [b - a for a, b in zip(values, values[1:])]
    return statistics.median(steps) if steps else None


def _column_bounds(columns, grid: list[Seg], pitch_x: float | None, right_edge: float) -> list[tuple[float, float]]:
    cuts = []
    for left, right in zip(columns, columns[1:]):
        lo, hi = max(t.cx for t in left), min(t.cx for t in right)
        cuts.append(_gutter(lo, hi, [(s.x0, s.x1) for s in grid if s.x1 > lo and s.x0 < hi]))
    if not cuts:
        return [(0.0, right_edge)]
    first = max(0.0, cuts[0] - pitch_x) if pitch_x else 0.0
    last = min(right_edge, cuts[-1] + pitch_x) if pitch_x else right_edge
    edges = [first, *cuts, last]
    return list(zip(edges, edges[1:]))


def _gutter(lo: float, hi: float, spans: list[tuple[float, float]]) -> float:
    """Middle of the widest stretch of (lo, hi) that no text covers; the midpoint if text covers all of it."""
    free, x = [], lo
    for a, b in sorted(spans):
        if a > x:
            free.append((x, min(a, hi)))
        x = max(x, b)
        if x >= hi:
            break
    if x < hi:
        free.append((x, hi))
    if not free:
        return (lo + hi) / 2
    a, b = max(free, key=lambda f: f[1] - f[0])
    return (a + b) / 2


def _lines(segs: list[Seg]) -> list[list[Seg]]:
    """Segments grouped into lines (same top within half a text height), top to bottom, left to right."""
    lines: list[list[Seg]] = []
    for s in sorted(segs, key=lambda s: (s.top, s.x0)):
        if lines and abs(s.top - lines[-1][0].top) < 0.5 * s.size:
            lines[-1].append(s)
        else:
            lines.append([s])
    return [sorted(line, key=lambda s: s.x0) for line in lines]
