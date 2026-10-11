"""Partition-types sheets: find the wall type tags and group the text around each one into a block.

A tag is a short standalone token like A1, P-3, 1A or 3B (optionally written "TYPE A1"). Tags on a sheet are
laid out in columns (a grid of wall-type cells, or one column down a schedule). Column boundaries sit in the
widest whitespace gutter between neighbouring tag columns. Each other segment goes to the nearest tag at or
above it in its column, within one row pitch, so a cell's notes stay with its own tag. Blocks with no
stud/board wording (detail bubbles, grid labels) are dropped.

Schedule layout (WCU A003): big wall-family labels (SA, SB, ...) head grid cells holding a section with
callouts over a schedule table whose columns are the wall types (SA4, SC2, SC4) and whose rows are STUD SIZE,
ACTUAL DIMENSION 'X', FIRE RATING, ... . The same column grouping runs with the family labels as anchors, and
each tag that heads a schedule column becomes one block: the family's callouts plus that column's values,
row by row. Tags outside every family (a tag legend) are ignored there.
"""
import re
import statistics
from dataclasses import dataclass

from extract.pdftext import Seg
from extract.scale import normalize
from extract.sheetinfo import PageText

TAG = re.compile(r"(?:(?:WALL|PARTITION)\s+)?(?:TYPE\s+)?(?P<tag>[A-Z]{1,2}-?\d{1,2}[A-Z]?|\d{1,2}[A-Z])")
NOT_TAG = re.compile(r"R-\d{2}")  # insulation R-values (R-19)
FAMILY = re.compile(r"[A-Z]{1,3}")
ROW_LABEL = re.compile(
    r"(?:STUD\s+(?:SIZE|GAUGE|SPACING)|ACTUAL\s+DIMENSION|DIMENSION|FIRE\s+RATING|(?:ACOUSTICAL|SOUND)\s+RATING|"
    r"STC|UL\b|GAUGE|SPACING|LAYERS?|HEIGHT)"
)
PARTITION_WORDS = re.compile(
    r"STUD|\bGA\b|GAUGE|\bMIL\b|GYP|\bGWB\b|BOARD|\bBD\b|LAYER|O\.?\s?C\b|\bMTL\b|METAL|FRAMING|SHAFT|@"
)


@dataclass
class Block:
    tag: str
    tag_seg: Seg
    segs: list[Seg]  # the text grouped with the tag, tag excluded
    body: str | None = None  # set for a schedule column: the family callouts, then the column row by row

    @property
    def text(self) -> str:
        return "\n".join([self.tag, self.body or _join(self.segs)])

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
    families = _families(p, tags)
    return (families and _schedule_blocks(p, tags, families)) or _tag_blocks(p, tags)


def _tag_blocks(p: PageText, tags: list[Seg]) -> list[Block]:
    tag_ids = {id(t) for t in tags}
    owned = _group(p, tags, [s for s in p.segs if id(s) not in tag_ids and not p.in_title_block(s)])
    best: dict[str, Block] = {}
    for t in tags:
        b = Block(tag_of(t), t, owned[id(t)])
        if not PARTITION_WORDS.search(normalize(b.text)):
            continue
        if b.tag not in best or len(b.text) > len(best[b.tag].text):  # a tag drawn twice: keep the fuller block
            best[b.tag] = b
    return sorted(best.values(), key=lambda b: (b.tag_seg.top, b.tag_seg.x0))


def _families(p: PageText, tags: list[Seg]) -> list[Seg]:
    """Wall-family labels: short all-letter text that prefixes tags (SA for SA4), the largest copy of each."""
    names = {tag_of(t) for t in tags}
    best: dict[str, Seg] = {}
    for s in p.segs:
        f = normalize(s.text).strip()
        if p.in_title_block(s) or not FAMILY.fullmatch(f):
            continue
        if any(n.startswith(f) and re.match(r"-?\d", n[len(f):]) for n in names):
            if f not in best or s.size > best[f].size:
                best[f] = s
    return list(best.values())


def _schedule_blocks(p: PageText, tags: list[Seg], families: list[Seg]) -> list[Block]:
    tag_ids, fam_ids = {id(t) for t in tags}, {id(f) for f in families}
    owned = _group(p, families, [s for s in p.segs if id(s) not in fam_ids and not p.in_title_block(s)])
    blocks = []
    for fam in families:
        name, segs = normalize(fam.text).strip(), owned[id(fam)]
        labels = [s for s in segs if ROW_LABEL.match(normalize(s.text).strip())]
        table: set[int] = set()
        columns = []
        for t in (s for s in segs if id(s) in tag_ids and tag_of(s).startswith(name)):
            rows = sorted((lab for lab in labels if lab.x1 < t.x0 and t.top < lab.top <= t.top + 200), key=lambda r: r.top)
            if len(rows) < 2:
                continue
            header = [s for s in segs if abs(s.top - t.top) <= 2 and (id(s) in tag_ids or _is_dash(s))]
            xs = sorted(h.cx for h in header)
            half = min((b - a for a, b in zip(xs, xs[1:])), default=80) / 2
            bottom = max(r.bottom for r in rows) + 1.3 * t.size
            left = min(r.x0 for r in rows) - 5
            cells = [s for s in segs if t.bottom - 1 <= s.top <= bottom and s.x0 >= left]
            table |= {id(s) for s in [*cells, *header, *rows]}
            values = [
                s for s in cells
                if s not in rows and not _is_dash(s) and id(s) not in tag_ids
                and abs(s.cx - t.cx) < half and min(header, key=lambda h: abs(h.cx - s.cx)) is t
            ]
            columns.append((t, rows, values))
        callouts = [s for s in segs if id(s) not in table and id(s) not in tag_ids and not _is_dash(s)]
        for t, rows, values in columns:
            by_row: dict[int, list[Seg]] = {id(r): [] for r in rows}
            for v in values:
                by_row[id(min(rows, key=lambda r: abs(r.cy - v.cy)))].append(v)
            cells = [f"{r.text}: {_join(by_row[id(r)], ' ')}" for r in rows if by_row[id(r)]]
            tag = tag_of(t)
            body = "\n".join([f"WALL FAMILY {name} CALLOUTS:", _join(callouts), f"SCHEDULE COLUMN {tag}:", *cells])
            blocks.append(Block(tag, t, values, body=body))
    return sorted(blocks, key=lambda b: (b.tag_seg.top, b.tag_seg.x0))


def _is_dash(s: Seg) -> bool:
    return s.text.strip() in ("-", "–", "—")


def _join(segs: list[Seg], sep: str = "\n") -> str:
    return sep.join(" ".join(s.text for s in line) for line in _lines(segs))


def _group(p: PageText, anchors: list[Seg], text: list[Seg]) -> dict[int, list[Seg]]:
    """id(anchor) -> the segments of `text` in its cell: its column, at or below it, within one row pitch."""
    tol = 0.5 * max(a.size for a in anchors)
    columns = _columns(anchors, tol)
    pitch_x = _median_step([statistics.fmean(a.cx for a in col) for col in columns])
    pitch_y = _median_step([a.top for col in columns for a in col], per=columns)
    reach_y = pitch_y or pitch_x or p.height  # how far below its anchor a cell's text can sit

    grid = [s for s in text if min(a.top for a in anchors) - tol <= s.top < max(a.top for a in anchors) + reach_y]
    right_edge = p.tb_corner[0] if p.tb_corner and p.tb_corner[1] == 0 else p.width
    bounds = _column_bounds(columns, grid, pitch_x, right_edge)

    owned: dict[int, list[Seg]] = {id(a): [] for a in anchors}
    for s in text:
        col = next((c for c, (lo, hi) in zip(columns, bounds) if lo <= s.cx < hi), None)
        above = [a for a in col or [] if a.top - tol <= s.top]
        if not above:
            continue
        a = max(above, key=lambda a: a.top)
        if s.top - a.top < reach_y:
            owned[id(a)].append(s)
    return owned


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
