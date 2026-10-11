"""Sheet number, sheet title, scale and kind from a page's text segments.

Title blocks sit in the right strip or along the bottom. The sheet number is the biggest
sheet-number-shaped token there; the title is the biggest non-boilerplate text near it. Text that
repeats in the title block of most sheets (firm, project, address) is boilerplate, which is why
naming runs after a first pass over every page of the set.
"""
import re
from collections import Counter
from dataclasses import dataclass

from extract.pdftext import Seg
from extract.scale import Scale, find_scales, normalize, says_as_noted

SHEET_NO = re.compile(r"[A-Z]{1,3}[-. ]?\d{1,3}(?:[.-]\d{1,3})?[A-Z]?")
SHEET_LABEL = re.compile(r"\b(?:SHEET|DWG|DRAWING)\s*(?:NO\b|NUMBER|#)|^SHEET:?$")
TITLE_LABEL = re.compile(r"\b(?:SHEET|DRAWING|DWG)\s+(?:TITLE|NAME)\b|^TITLE:?$")
SCALE_LABEL = re.compile(r"^SCALE\b")
BOILERPLATE = re.compile(
    r"^(?:SCALE|DATE|DRAWN|CHECKED|APPROVED|PROJECT\s*(?:NO|NUMBER|#)|JOB\s*(?:NO|NUMBER|#)|COMM\s*NO|"
    r"SHEET|REV\b|REVISION|ISSUE|ISSUED|SEAL|DESCRIPTION|NO\.|MARK\b|CONSULTANT|KEY\s*PLAN|NORTH|COPYRIGHT|"
    r"PRINTED|PLOT|FILE|CAD|PERMIT|BID|CONSTRUCTION\s+DOCUMENTS|NOT\s+FOR\s+CONSTRUCTION)\b"
    r"|©|@|WWW\.|\.COM\b|\(\d{3}\)|\d{3}[-.]\d{3}[-.]\d{4}|^\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}$"
)
TITLE_WORDS = re.compile(
    r"\b(?:PLANS?|ELEVATIONS?|SECTIONS?|DETAILS?|SCHEDULES?|PARTITIONS?|TYPES|ASSEMBL(?:Y|IES)|NOTES|LEGENDS?|"
    r"COVER|INDEX|CEILING|ROOF|SITE|ENLARGED|FINISH(?:ES)?|DOORS?|WINDOWS?|WALLS?|FLOOR|LEVEL|BASEMENT|"
    r"FOUNDATION|FRAMING|STAIRS?|INTERIOR|EXTERIOR|DEMOLITION|LIFE\s+SAFETY|CODE)\b"
)

KIND_RULES: list[tuple[str, re.Pattern]] = [
    ("partition_types", re.compile(r"PARTITION|WALL\s+TYPES?|ASSEMBL(?:Y|IES)")),
    ("reflected_ceiling", re.compile(r"REFLECTED|CEILING|\bRCP\b")),
    ("schedules", re.compile(r"SCHEDULE")),
    ("details", re.compile(r"DETAIL")),
    ("elevations", re.compile(r"ELEVATION|SECTION")),
    ("other", re.compile(r"\bSITE\b|ROOF\s+PLAN|COVER|INDEX|LIFE\s+SAFETY")),
    ("floor_plan", re.compile(r"\bPLANS?\b")),
]


@dataclass
class PageText:
    width: float
    height: float
    segs: list[Seg]
    bookmark: str = ""
    label: str = ""
    tb_corner: tuple[float, float] | None = None  # (min x, min top) of the title block, once located

    def in_title_block(self, s: Seg) -> bool:
        if self.tb_corner:
            return s.cx >= self.tb_corner[0] and s.cy >= self.tb_corner[1]
        return self.in_edge_band(s)

    def in_edge_band(self, s: Seg) -> bool:
        return s.cx > 0.72 * self.width or s.cy > 0.82 * self.height


@dataclass
class SheetName:
    sheet_no: str | None
    title: str | None
    source: str  # bookmark | title_block | none


def common_title_block_text(pages: list[PageText]) -> set[str]:
    """Title-block text that repeats on at least half the sheets (min 3): firm, project, address."""
    if len(pages) < 3:
        return set()
    counts: Counter[str] = Counter()
    for p in pages:
        counts.update({normalize(s.text) for s in p.segs if p.in_title_block(s)})
    need = max(3, len(pages) / 2)
    return {t for t, c in counts.items() if c >= need}


def locate_title_blocks(pages: list[PageText], common: set[str]) -> None:
    """Set each page's tb_corner from where the set's repeating title-block text sits on it.

    Repeating text only in the right strip -> a right strip, corner (x, 0); only along the bottom -> a bottom
    band, corner (0, y); only in the bottom-right corner -> a box. Without this, view titles near the bottom
    of a right-strip sheet (or the right of a bottom-band sheet) read as title block. Pages with no repeating
    text keep the edge-band guess.
    """
    for p in pages:
        anchors = [s for s in p.segs if p.in_edge_band(s) and len(t := normalize(s.text).strip()) >= 3 and t in common]
        right = [s for s in anchors if s.cx > 0.72 * p.width]
        bottom = [s for s in anchors if s.cy > 0.82 * p.height]
        corner = [s for s in right if s in bottom]
        only_right, only_bottom = len(right) - len(corner), len(bottom) - len(corner)
        pad = 0.01 * max(p.width, p.height)
        if only_right > only_bottom:
            p.tb_corner = (max(0.0, min(s.x0 for s in right) - pad), 0.0)
        elif only_bottom > only_right:
            p.tb_corner = (0.0, max(0.0, min(s.top for s in bottom) - pad))
        elif corner:
            p.tb_corner = (max(0.0, min(s.x0 for s in corner) - pad), max(0.0, min(s.top for s in corner) - pad))


def title_block_text(p: PageText, common: set[str], limit: int = 1500) -> str:
    """This sheet's own title-block text (repeating boilerplate dropped) in reading order, for Jev."""
    own = [s for s in p.segs if p.in_title_block(s) and normalize(s.text).strip() not in common]
    return " | ".join(s.text for s in sorted(own, key=lambda s: (s.top, s.x0)))[:limit]


def _sheet_no_in(text: str) -> str | None:
    t = normalize(text).strip()
    if SHEET_NO.fullmatch(t):
        return t.replace(" ", "")
    return None


def find_sheet_no(p: PageText) -> Seg | None:
    tb = [s for s in p.segs if p.in_title_block(s)]
    if not tb:
        return None
    max_size = max(s.size for s in tb)
    labels = [s for s in tb if SHEET_LABEL.search(normalize(s.text))]
    best, best_score = None, 0.0
    for s in tb:
        if not _sheet_no_in(s.text):
            continue
        score = 2 * s.size / max_size + 0.5 * (s.cx / p.width) + 0.5 * (s.cy / p.height)
        if any(lab.gap_to(s) < 2.0 * max(s.size, lab.size) for lab in labels):
            score += 0.6
        if score > best_score:
            best, best_score = s, score
    return best


def _excluded(s: Seg, common: set[str], sheet_seg: Seg | None) -> bool:
    t = normalize(s.text).strip()
    if s is sheet_seg or len(t) < 3 or t in common:
        return True
    if BOILERPLATE.search(t) or TITLE_LABEL.search(t) or find_scales(t) or says_as_noted(t):
        return True
    digits = sum(c.isdigit() for c in t)
    return digits / len(t) > 0.4


def find_title(p: PageText, common: set[str], sheet_seg: Seg | None) -> str | None:
    tb = [s for s in p.segs if p.in_title_block(s)]
    cands = [s for s in tb if not _excluded(s, common, sheet_seg)]
    if not cands:
        return None
    max_size = max(s.size for s in cands)
    title_labels = [s for s in tb if TITLE_LABEL.search(normalize(s.text))]

    def score(s: Seg) -> float:
        t = normalize(s.text)
        letters = [c for c in t if c.isalpha()]
        sc = 2 * s.size / max_size + 0.3 * (sum(c.isupper() for c in letters) / max(1, len(letters)))
        if TITLE_WORDS.search(t):
            sc += 0.8
        if sheet_seg is not None:
            sc += 0.6 * max(0.0, 1 - s.gap_to(sheet_seg) / 400)
        if any(lab.bottom <= s.top + 1 and lab.gap_to(s) < 2.5 * s.size for lab in title_labels):
            sc += 0.5
        return sc

    best = max(cands, key=score)
    return " ".join(x.text for x in _title_block_run(best, cands))


def _title_block_run(best: Seg, cands: list[Seg]) -> list[Seg]:
    """The best line plus same-size lines stacked directly above/below it (multi-line titles)."""
    def joins(a: Seg, b: Seg) -> bool:  # b directly below a
        overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
        return (
            abs(a.size - b.size) <= 0.15 * a.size
            and -0.2 * a.size <= b.top - a.bottom <= 0.8 * a.size
            and overlap > 0
        )

    run = [best]
    pool = [c for c in cands if c is not best]
    grew = True
    while grew:
        grew = False
        for c in list(pool):
            if joins(run[-1], c):
                run.append(c)
            elif joins(c, run[0]):
                run.insert(0, c)
            else:
                continue
            pool.remove(c)
            grew = True
    return run


def split_bookmark(text: str) -> tuple[str | None, str | None]:
    """'A-101 - FIRST FLOOR PLAN' -> ('A-101', 'FIRST FLOOR PLAN'); 'First Floor Plan' -> (None, ...)."""
    t = text.strip()
    m = re.match(r"^\s*([A-Za-z]{1,3}[-. ]?\d{1,3}(?:[.-]\d{1,3})?[A-Za-z]?)\b\s*[-:–—|]?\s*(.*)$", t)
    if m and _sheet_no_in(m[1]):
        return _sheet_no_in(m[1]), (m[2].strip() or None)
    return None, (t or None)


def sheet_name(p: PageText, common: set[str]) -> SheetName:
    sheet_seg = find_sheet_no(p)
    tb_no = _sheet_no_in(sheet_seg.text) if sheet_seg else None
    tb_title = find_title(p, common, sheet_seg)
    if p.bookmark:
        bm_no, bm_title = split_bookmark(p.bookmark)
        return SheetName(bm_no or tb_no or _sheet_no_in(p.label), bm_title or tb_title, "bookmark")
    no = tb_no or _sheet_no_in(p.label)
    if no or tb_title:
        return SheetName(no, _clean_title(tb_title), "title_block")
    return SheetName(None, None, "none")


def _clean_title(t: str | None) -> str | None:
    if not t:
        return None
    t = re.sub(r"\s+", " ", t).strip(" -:")
    return t[:200] or None


def pick_scale(p: PageText) -> dict | None:
    """Title-block scale if it states one; otherwise the most common view-title scale on the sheet."""
    tb = [s for s in p.segs if p.in_title_block(s)]
    for lab in (s for s in tb if SCALE_LABEL.search(normalize(s.text))):
        own = find_scales(lab.text)
        if own:
            return own[0].as_doc("title_block", 0.9)
        if says_as_noted(lab.text):
            break
        near = sorted((s for s in tb if s is not lab and lab.gap_to(s) < 3 * lab.size), key=lab.gap_to)
        for s in near[:2]:
            if says_as_noted(s.text):
                break
            got = find_scales(s.text)
            if got:
                return got[0].as_doc("title_block", 0.85)

    views: list[Scale] = [sc for s in p.segs if not p.in_title_block(s) for sc in find_scales(s.text)]
    numeric = [s for s in views if s.pt_per_ft]
    if numeric:
        counts = Counter(numeric)
        top = max(counts.values())
        best = min((s for s, c in counts.items() if c == top), key=lambda s: s.pt_per_ft)
        labels = [s.label for s, _ in counts.most_common()]
        return best.as_doc("view_title", 0.75 if len(counts) == 1 else 0.55, labels)

    anywhere = [sc for s in p.segs for sc in find_scales(s.text)]
    if anywhere:
        return anywhere[0].as_doc("title_block" if any(p.in_title_block(s) for s in p.segs) else "view_title", 0.5)
    return None


def guess_kind(title: str | None) -> str:
    t = normalize(title or "")
    for kind, rx in KIND_RULES:
        if rx.search(t):
            return kind
    return "other"
