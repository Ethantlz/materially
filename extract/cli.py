"""Print the sheet names and scales detected in a PDF, without touching MongoDB.

    uv run python -m extract.cli path/to/set.pdf
"""
import sys
import time
from pathlib import Path

from extract.ingest import read_pages, sheet_docs


def main(paths: list[str]) -> None:
    t0 = time.perf_counter()
    files = [(Path(p).name, read_pages(Path(p))) for p in paths]
    docs = sheet_docs("cli", files)
    ms = (time.perf_counter() - t0) * 1000
    for d in docs:
        sc = d["scale"] or {}
        print(
            f"{d['page']:>4}  {d['sheet_no'] or '-':<10} {(d['title'] or '-')[:44]:<44} "
            f"{d['kind']:<17} {sc.get('label', '-'):<16} {sc.get('source', ''):<11} {d['name_source']}"
        )
    print(f"{len(docs)} pages in {ms:.0f} ms")


if __name__ == "__main__":
    main(sys.argv[1:])
