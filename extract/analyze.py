"""After pass 1: Jev sorts each sheet, then partition sheets become conditions and floor plans become rooms.

This runs once the names are written (so the UI shows them at once) and updates the sheet docs in place.
Sorting: jev.choice over SHEET_KINDS. Agreed -> kind and routing; disagreed or Jev down -> keep the
title-keyword kind, store routing, and park the sheet in review_queue. Parsing: one
run_task("parse_partition") per type-tag block, written with insert_or_review, so a condition MongoDB
rejects lands in review_queue with the reason. Caps are process-wide, shared by every job.
"""
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from bson import ObjectId
from pymongo.database import Database

from app import jev, router
from extract import conditions
from app.db import insert_or_review, oid, to_review
from contracts.schemas import SHEET_KINDS
from extract.partitions import Block, partition_blocks
from extract.rooms import find_rooms
from extract.sheetinfo import PageText
from extract.store import insert_all

JEV_AT_ONCE = 8  # jev.choice calls in flight
TASKS_AT_ONCE = 4  # run_task calls in flight: LiteLLM caps DeepSeek at 30 requests/min
_jev_slots = threading.BoundedSemaphore(JEV_AT_ONCE)
_task_slots = threading.BoundedSemaphore(TASKS_AT_ONCE)

QUESTION = "Which kind of drawing sheet is this?"
# One line per kind, so Jev files sheets the way the title-keyword rules do (sections are elevations; site,
# roof and cover sheets are other).
KIND_OPTIONS = {
    "partition_types": "partition types, wall types or wall assemblies",
    "floor_plan": "floor plans and enlarged floor plans (not ceiling, roof or site plans)",
    "reflected_ceiling": "reflected ceiling plans (RCP)",
    "elevations": "exterior or interior elevations, building or wall sections",
    "details": "construction details",
    "schedules": "door, window, finish or room schedules",
    "other": "cover sheet, drawing index, site, civil, roof, code or life safety plans, and anything else",
}
assert list(KIND_OPTIONS) == SHEET_KINDS


def sort_state(sheet: dict) -> str:
    return f"Sheet title: {sheet.get('title') or '(none)'}\nTitle block: {sheet.get('title_block_text') or '(none)'}"


def sort_sheet(db: Database, job_id: str, sheet: dict) -> str:
    """Ask Jev for the sheet's kind and update the sheet doc in place. Returns the kind it ends up with."""
    keyword_kind = sheet["kind"]
    reason = None
    try:
        with _jev_slots:
            r = jev.choice(sort_state(sheet), KIND_OPTIONS, QUESTION, task="jev.sheet_kind", job_id=job_id, db=db)
    except Exception as e:  # JevError when every System One model failed, or an unexpected answer
        kind, routing = keyword_kind, {"method": "title_keywords", "error": f"{type(e).__name__}: {e}"[:500]}
        reason = f"Jev failed, kept the title-keyword kind {keyword_kind}: {e}"
    else:
        if r.get("agreed") and r.get("choice") in SHEET_KINDS:
            kind, routing = r["choice"], {**r, "method": "jev"}
        else:
            kind, routing = keyword_kind, {**r, "method": "jev", "fallback": "title_keywords"}
            answers = " vs ".join(str(a.get("choice")) for a in r.get("answers", []))
            reason = f"Jev's two asks disagree ({answers}); kept the title-keyword kind {keyword_kind}"
    db.sheets.update_one({"_id": sheet["_id"]}, {"$set": {"kind": kind, "routing": routing}})
    sheet.update(kind=kind, routing=routing)
    if reason:
        to_review(
            db,
            "extract.sheet_kind",
            {"job_id": job_id, "sheet_id": str(sheet["_id"]), "page": sheet["page"], "sheet_no": sheet.get("sheet_no"),
             "title": sheet.get("title"), "keyword_kind": keyword_kind, "routing": routing},
            reason,
        )
    db.jobs.update_one({"_id": oid(job_id)}, {"$inc": {"pages_sorted": 1}})
    return kind


def parse_block(db: Database, job_id: str, sheet: dict, block: Block) -> ObjectId | None:
    """One type tag's block -> a conditions doc, or a review_queue row (model failed, or MongoDB rejected it)."""
    sheet_id = str(sheet["_id"])
    source = {"sheet": sheet_id, "page": sheet["page"], "bbox": block.bbox}
    try:
        with _task_slots:
            out = router.run_task("parse_partition", block.text, "conditions", job_id=job_id, db=db)
    except router.TaskFailed as e:
        if isinstance(e.result, dict):  # an answer that failed the contract: MongoDB's validator rejects it into review
            routing = {"task": "parse_partition", "model": e.model, "escalated": True}
            return insert_or_review(db, "conditions", {**e.result, "job_id": job_id, "source": source, "routing": routing},
                                    "extract.partition")
        to_review(
            db,
            "extract.partition",
            {"job_id": job_id, "sheet": sheet_id, "page": sheet["page"], "type_tag": block.tag, "text": block.text, "raw": e.raw},
            e.reason,
        )
        return None
    doc = {
        **out["result"],
        "job_id": job_id,
        "source": source,
        "routing": {"task": "parse_partition", "model": out["model"], "escalated": out["escalated"]},
    }
    return conditions.insert(db, doc, "extract.partition")


def parse_blocks(db: Database, job_id: str, sheet: dict, blocks: list[Block]) -> list[ObjectId | None]:
    """Parse blocks from one sheet, TASKS_AT_ONCE at a time."""
    with ThreadPoolExecutor(TASKS_AT_ONCE) as pool:
        return list(pool.map(lambda b: parse_block(db, job_id, sheet, b), blocks))


def parse_sheet(db: Database, job_id: str, sheet: dict, page: PageText) -> list[ObjectId | None]:
    """Parse every type-tag block on one sheet."""
    return parse_blocks(db, job_id, sheet, partition_blocks(page))


def add_rooms(db: Database, job_id: str, sheet: dict, page: PageText) -> int:
    docs = [{"job_id": job_id, "sheet": str(sheet["_id"]), **room} for room in find_rooms(page)]
    return sum(i is not None for i in insert_all(db, "rooms", docs, "extract.rooms"))


def analyze(db: Database, job_id: str, items: list[tuple[dict, PageText]]) -> None:
    """Sort every sheet; as each one is sorted, read its rooms or queue its partition blocks for parsing."""
    with ThreadPoolExecutor(JEV_AT_ONCE) as sorters, ThreadPoolExecutor(TASKS_AT_ONCE) as parsers:
        sorting = {sorters.submit(sort_sheet, db, job_id, sheet): (sheet, page) for sheet, page in items}
        parsing = []
        for done in as_completed(sorting):
            sheet, page = sorting[done]
            kind = done.result()
            if kind == "floor_plan":
                add_rooms(db, job_id, sheet, page)
            elif kind == "partition_types":
                parsing += [parsers.submit(parse_block, db, job_id, sheet, b) for b in partition_blocks(page)]
        for f in parsing:
            f.result()
