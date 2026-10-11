"""Drawing scales: parse scale notes and convert them to PDF points per real foot.

PDF user space is 72 pt per paper inch, so a scale of P paper-inches = F real-feet is 72*P/F pt/ft.
Handles architectural (1/8" = 1'-0", 1 1/2" = 1'-0"), engineering (1" = 20'), metric (SCALE 1:100)
and NTS / NOT TO SCALE.
"""
import re
from dataclasses import dataclass
from fractions import Fraction

_TRANS = str.maketrans(
    {
        "″": '"', "“": '"', "”": '"', "„": '"',
        "′": "'", "‘": "'", "’": "'", "`": "'",
        "–": "-", "—": "-", "‐": "-",
        "½": " 1/2", "¼": " 1/4", "¾": " 3/4", "⅛": " 1/8", "⅜": " 3/8", "⅝": " 5/8", "⅞": " 7/8",
        "⁄": "/",
    }
)

# whole part of a mixed number is 1..11 so "ROOM 101 1/8\"" can't read as 101 1/8
_PAPER = r"(?<![\d/.])(?P<paper>(?:1[01]|[1-9])\s*[- ]\s*\d{1,2}/\d{1,2}|\d{1,2}/\d{1,3}|\d{1,2}(?:\.\d+)?)"
IMPERIAL = re.compile(_PAPER + r"\s*\"\s*=\s*(?P<ft>\d{1,4}(?:\.\d+)?)\s*'(?P<inch>\s*-?\s*0\s*\"?)?")
METRIC = re.compile(r"SCALE\s*:?\s*1\s*:\s*(?P<d>\d{1,5})(?![\d:])")
NTS = re.compile(r"(?<![A-Z])N\.?\s?T\.?\s?S\.?(?![A-Z])|NOT\s+TO\s+SCALE")
AS_NOTED = re.compile(r"AS\s+(?:NOTED|SHOWN|INDICATED)")


@dataclass(frozen=True)
class Scale:
    label: str
    pt_per_ft: float | None
    ratio: float | None

    def as_doc(self, source: str, confidence: float, candidates: list[str] | None = None) -> dict:
        return {
            "label": self.label,
            "pt_per_ft": self.pt_per_ft,
            "ratio": self.ratio,
            "source": source,
            "confidence": round(confidence, 2),
            "candidates": candidates or [self.label],
        }


NOT_TO_SCALE = Scale("NTS", None, None)


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(_TRANS).replace("''", '"').upper())


def _fraction(s: str) -> Fraction:
    s = s.strip()
    m = re.fullmatch(r"(\d+)\s*[- ]\s*(\d+)/(\d+)", s)
    if m:
        return int(m[1]) + Fraction(int(m[2]), int(m[3]))
    if "/" in s:
        n, d = s.split("/")
        return Fraction(int(n), int(d))
    return Fraction(s)


def _fmt(f: Fraction) -> str:
    whole, rem = divmod(f.numerator, f.denominator)
    if rem == 0:
        return str(whole)
    frac = f"{rem}/{f.denominator}"
    return f"{whole} {frac}" if whole else frac


def imperial(paper_in: Fraction, real_ft: Fraction) -> Scale:
    label = f"{_fmt(paper_in)}\" = 1'-0\"" if real_ft == 1 else f"{_fmt(paper_in)}\" = {_fmt(real_ft)}'"
    return Scale(label, round(float(72 * paper_in / real_ft), 4), round(float(real_ft * 12 / paper_in), 4))


def metric(denominator: int) -> Scale:
    return Scale(f"1:{denominator}", round(72 * 12 / denominator, 4), float(denominator))


def find_scales(text: str) -> list[Scale]:
    """Every scale written in `text`, in order. NTS counts as a scale with no pt_per_ft."""
    t = normalize(text)
    found: list[tuple[int, Scale]] = []
    for m in IMPERIAL.finditer(t):
        try:
            paper, ft = _fraction(m["paper"]), Fraction(m["ft"])
        except (ValueError, ZeroDivisionError):
            continue
        if paper <= 0 or ft <= 0 or ft > 2000:
            continue
        found.append((m.start(), imperial(paper, ft)))
    for m in METRIC.finditer(t):
        d = int(m["d"])
        if d > 0:
            found.append((m.start(), metric(d)))
    for m in NTS.finditer(t):
        found.append((m.start(), NOT_TO_SCALE))
    return [s for _, s in sorted(found, key=lambda p: p[0])]


def says_as_noted(text: str) -> bool:
    return bool(AS_NOTED.search(normalize(text)))


def parse_label(label: str) -> Scale | None:
    """Parse a user-entered scale: 1/8" = 1'-0", 1/8, 1 1/2, 1" = 20', 1:100, NTS."""
    t = normalize(label).strip()
    if not t:
        return None
    scales = find_scales(t) or find_scales(f"SCALE {t}")
    if scales:
        return scales[0]
    if re.fullmatch(r"(?:1[01]|[1-9])\s*[- ]\s*\d+/\d+|\d+/\d+|\d+(?:\.\d+)?", t):  # bare paper inches per foot
        try:
            return imperial(_fraction(t), Fraction(1))
        except (ValueError, ZeroDivisionError):
            return None
    return None


COMMON_LABELS = [
    "NTS",
    "1/16\" = 1'-0\"", "3/32\" = 1'-0\"", "1/8\" = 1'-0\"", "3/16\" = 1'-0\"", "1/4\" = 1'-0\"",
    "3/8\" = 1'-0\"", "1/2\" = 1'-0\"", "3/4\" = 1'-0\"", "1\" = 1'-0\"", "1 1/2\" = 1'-0\"", "3\" = 1'-0\"",
    "1\" = 10'", "1\" = 20'", "1\" = 30'", "1\" = 40'", "1\" = 50'", "1\" = 60'", "1\" = 100'",
    "1:50", "1:100", "1:200",
]
