from extract.pdftext import Seg


def seg(text: str, x0: float, top: float, size: float = 10.0) -> Seg:
    """A segment with a Helvetica-ish width (0.6 em per character)."""
    return Seg(text, x0, top, x0 + 0.6 * size * len(text), top + size, size)
