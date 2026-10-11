"""Floor plans: room labels. A room number (101, 204A) stacked directly under (or over) an uppercase name is a
room; label_pt is the centre of the whole label in PDF points. The first label per number wins on a sheet."""
import re

from extract.pdftext import Seg
from extract.scale import find_scales, normalize
from extract.sheetinfo import PageText

ROOM_NO = re.compile(r"\d{3,4}[A-Z]?")
NOT_NAMES = {"UP", "DN", "DOWN", "EQ", "TYP", "SIM", "NORTH", "SCALE", "NTS"}
MAX_NAME_LINES = 3


def _is_number(s: Seg) -> bool:
    return bool(ROOM_NO.fullmatch(normalize(s.text).strip()))


def _is_name(s: Seg) -> bool:
    t = normalize(s.text).strip()
    chars = [c for c in t if not c.isspace()]
    letters = [c for c in chars if c.isalpha()]
    return (
        len(letters) >= 2
        and s.text == s.text.upper()
        and len(letters) / len(chars) >= 0.6
        and len(t) <= 40
        and t not in NOT_NAMES
        and not _is_number(s)
        and not find_scales(t)
    )


def _stacked(upper: Seg, lower: Seg) -> bool:
    """`lower` sits directly under `upper`, overlapping it horizontally, in a similar size."""
    size = max(upper.size, lower.size)
    return (
        0 <= lower.top - upper.bottom <= 0.6 * size
        and min(upper.x1, lower.x1) - max(upper.x0, lower.x0) > 0
        and 0.5 <= upper.size / lower.size <= 2
    )


def _name_lines(number: Seg, names: list[Seg]) -> list[Seg]:
    """Name lines stacked above the number, else below it, in top-to-bottom order."""
    for above in (True, False):
        run, cur = [], number
        while len(run) < MAX_NAME_LINES:
            nxt = next((n for n in names if n not in run and (_stacked(n, cur) if above else _stacked(cur, n))), None)
            if nxt is None:
                break
            run.append(nxt)
            cur = nxt
        if run:
            return run[::-1] if above else run
    return []


def find_rooms(p: PageText) -> list[dict]:
    """[{name, number, label_pt: [x, y]}] in reading order, one per room number."""
    segs = [s for s in p.segs if not p.in_title_block(s)]
    names = [s for s in segs if _is_name(s)]
    labels = []
    for number in (s for s in segs if _is_number(s)):
        lines = _name_lines(number, names)
        if lines:
            every = [*lines, number]
            box = (min(s.x0 for s in every), min(s.top for s in every), max(s.x1 for s in every), max(s.bottom for s in every))
            labels.append((box, " ".join(s.text.strip() for s in lines), normalize(number.text).strip()))
    rooms, seen = [], set()
    for (x0, top, x1, bottom), name, no in sorted(labels, key=lambda lab: (lab[0][1], lab[0][0])):
        if no in seen:
            continue
        seen.add(no)
        rooms.append({"name": name, "number": no, "label_pt": [round((x0 + x1) / 2, 1), round((top + bottom) / 2, 1)]})
    return rooms
