"""Ingest a drawing set: read every page, name it, set its scale, then render images and analyze.

Pass 1 (fast, text only) writes one `sheets` doc per page in one batch, so names and scales show up within
seconds. Then two things run side by side and update those docs in place: pass 2 renders the page image
and thumbnail for each sheet, and extract.analyze has Jev sort each sheet and reads partition types into
conditions and floor plans into rooms. The job is "ready" when both are done.
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pypdfium2 as pdfium
from pymongo.database import Database

from app.db import now, oid
from extract.analyze import analyze
from extract.pdftext import bookmarks_by_page, page_label, page_segments
from extract.sheetinfo import (
    PageText,
    common_title_block_text,
    guess_kind,
    locate_title_blocks,
    pick_scale,
    sheet_name,
    title_block_text,
)
from extract.store import insert_all

log = logging.getLogger("materially.ingest")

MAX_IMAGE_PX = 4000
IMAGE_SCALE = 1.5  # px per PDF point (108 dpi), capped so the long side stays under MAX_IMAGE_PX
THUMB_WIDTH = 480


def read_pages(pdf_path: Path) -> list[PageText]:
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        marks = bookmarks_by_page(pdf)
        pages = []
        for i in range(len(pdf)):
            page = pdf[i]
            try:
                w, h = page.get_size()
                pages.append(PageText(w, h, page_segments(page), marks.get(i, ""), page_label(pdf, i)))
            finally:
                page.close()
        return pages
    finally:
        pdf.close()


def sheet_docs(job_id: str, files: list[tuple[str, list[PageText]]]) -> list[dict]:
    all_pages = [p for _, pages in files for p in pages]
    common = common_title_block_text(all_pages)
    locate_title_blocks(all_pages, common)
    common = common_title_block_text(all_pages)  # again, inside the located title blocks only
    docs, n = [], 0
    for file_name, pages in files:
        for i, p in enumerate(pages, 1):
            n += 1
            name = sheet_name(p, common)
            docs.append(
                {
                    "job_id": job_id,
                    "page": n,
                    "file": file_name,
                    "file_page": i,
                    "sheet_no": name.sheet_no,
                    "title": name.title,
                    "name_source": name.source,
                    "title_block_text": title_block_text(p, common),
                    "kind": guess_kind(name.title),
                    "routing": {"method": "title_keywords"},
                    "size_pt": [round(p.width, 2), round(p.height, 2)],
                    "scale": pick_scale(p),
                    "image_path": None,
                    "thumb_path": None,
                    "created_at": now(),
                }
            )
    return docs


def read_sheet_page(pdf_path: Path, file_page: int) -> PageText:
    """One page of a file, with its title block located against the rest of that file."""
    pages = read_pages(pdf_path)
    locate_title_blocks(pages, common_title_block_text(pages))
    return pages[file_page - 1]


def render(pdf_path: Path, file_page: int, out_dir: Path, stem: str) -> tuple[Path, Path, float]:
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        page = pdf[file_page - 1]
        w, h = page.get_size()
        scale = min(IMAGE_SCALE, MAX_IMAGE_PX / max(w, h))
        img = page.render(scale=scale).to_pil()
        page.close()
    finally:
        pdf.close()
    out_dir.mkdir(parents=True, exist_ok=True)
    full, thumb = out_dir / f"{stem}.png", out_dir / f"{stem}.thumb.png"
    img.save(full)
    img.thumbnail((THUMB_WIDTH, THUMB_WIDTH))
    img.save(thumb)
    return full, thumb, round(scale, 4)


def name_sheets(db: Database, job_id: str, pdf_paths: list[Path]) -> list[tuple[dict, PageText]]:
    """Pass 1: read, name and scale every page, and write the sheets docs in one batch.
    Returns (sheet doc, PageText) for each sheet written, for the passes after it."""
    files = [(p.name, read_pages(p)) for p in pdf_paths]
    db.jobs.update_one(
        {"_id": oid(job_id)},
        {"$set": {"status": "processing", "pages_total": sum(len(pages) for _, pages in files),
                  "files": [{"name": n, "pages": len(ps)} for n, ps in files]}},
    )
    docs = sheet_docs(job_id, files)
    ids = insert_all(db, "sheets", docs, source="extract.ingest")
    db.jobs.update_one({"_id": oid(job_id)}, {"$set": {"pages_named": sum(i is not None for i in ids)}})
    pages = [p for _, ps in files for p in ps]
    return [(doc, page) for doc, page, i in zip(docs, pages, ids) if i is not None]


def render_sheets(db: Database, job_id: str, pdf_paths: list[Path], data_dir: Path) -> None:
    """Pass 2: page image and thumbnail for each sheet."""
    by_name = {p.name: p for p in pdf_paths}
    pages_dir = data_dir / job_id / "pages"
    for i, sheet in enumerate(db.sheets.find({"job_id": job_id}).sort("page", 1), 1):
        full, thumb, scale = render(by_name[sheet["file"]], sheet["file_page"], pages_dir, f"{sheet['page']:04d}")
        db.sheets.update_one(
            {"_id": sheet["_id"]},
            {"$set": {"image_path": _url(full, data_dir), "thumb_path": _url(thumb, data_dir), "image_scale": scale}},
        )
        db.jobs.update_one({"_id": oid(job_id)}, {"$set": {"pages_rendered": i}})


def ingest(db: Database, job_id: str, pdf_paths: list[Path], data_dir: Path) -> None:
    job_oid = oid(job_id)
    try:
        items = name_sheets(db, job_id, pdf_paths)
        with ThreadPoolExecutor(max_workers=1) as background:
            analysis = background.submit(analyze, db, job_id, items)
            render_sheets(db, job_id, pdf_paths, data_dir)
            analysis.result()
        db.jobs.update_one({"_id": job_oid}, {"$set": {"status": "ready"}})
    except Exception as e:  # surface the failure on the job instead of losing it in a background thread
        log.exception("ingest failed for job %s", job_id)
        db.jobs.update_one({"_id": job_oid}, {"$set": {"status": "error", "error": f"{type(e).__name__}: {e}"[:500]}})


def _url(path: Path, data_dir: Path) -> str:
    return "/data/" + path.relative_to(data_dir).as_posix()

