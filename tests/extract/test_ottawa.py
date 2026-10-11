"""The Ottawa tender set: a public 10-sheet Canadian architectural set (gitignored; these tests skip without it).

View titles write SCALE and 1:100 in separate cells; partitions are a list-style ASSEMBLY SCHEDULE on A001
(P1 not used, P2 to P5), in metric.
"""
import os

import pytest

from app.db import now
from app.settings import ROOT
from extract.analyze import parse_sheet
from extract.ingest import name_sheets, read_pages, sheet_docs
from extract.partitions import partition_blocks
from extract.sheetinfo import common_title_block_text, locate_title_blocks
from tests.extract.helpers import forget_job

OTTAWA = ROOT.parent / "materially" / "fixtures" / "ottawa-arch-tender.pdf"
pytestmark = pytest.mark.skipif(not OTTAWA.exists(), reason=f"{OTTAWA} not on disk")


def test_ottawa_names_and_scales():
    docs = {d["sheet_no"]: d for d in sheet_docs("job", [(OTTAWA.name, read_pages(OTTAWA))])}
    assert {no: d["title"] for no, d in docs.items()} == {
        "A001": "COVER PAGE, DRAWING LIST, ASSEMBLY SCHEDULE",
        "A111": "DEMOLITION PLANS",
        "A112": "REFLECTED CEILING PLANS DEMOLITION",
        "A121": "CONSTRUCTION/ FINISH PLANS",
        "A131": "REFLECTED CEILING PLANS NEW",
        "A151": "EQUIPMENT/ FURNITURE PLANS",
        "A201": "INTERIOR ELEVATIONS",
        "A202": "INTERIOR ELEVATIONS",
        "A501": "DETAILS",
        "A601": "DOOR ELEVATIONS AND SCHEDULE",
    }
    labels = {no: (d["scale"] or {}).get("label") for no, d in docs.items()}
    assert {no: labels[no] for no in ("A111", "A121", "A201", "A202", "A501")} == {
        "A111": "1:100", "A121": "1:100", "A201": "1:50", "A202": "1:50", "A501": "1:5",
    }
    # A001's only view is the STAGING AREA LOCATION PLAN, drawn at 1:500
    assert labels["A001"] == "1:500" and docs["A001"]["scale"]["source"] == "view_title"
    assert all(docs[no]["scale"]["source"] == "view_title" for no in ("A111", "A121", "A201", "A202", "A501"))


def test_ottawa_assembly_schedule_blocks():
    pages = read_pages(OTTAWA)
    locate_title_blocks(pages, common_title_block_text(pages))
    blocks = {b.tag: b.text for b in partition_blocks(pages[0])}
    assert sorted(blocks) == ["P2", "P3", "P4", "P5"]
    assert "92 mm STEEL STUDS @ MAX. 400mm" in blocks["P2"] and "STAGING" not in blocks["P3"]
    assert "SEAL AROUND ALL PENETRATIONS" in blocks["P4"]
    assert "ULC W407" in blocks["P5"] and blocks["P5"].count("16mm TYPE X GYPSUM BOARD") == 2



@pytest.mark.skipif(not os.environ.get("LITELLM_API_KEY"), reason="needs LITELLM_API_KEY (run under infisical run)")
def test_ottawa_assembly_schedule_conditions(live_db, capsys):
    db = live_db
    job_id = str(db.jobs.insert_one({"name": "ottawa A001 test", "created_at": now(), "status": "processing"}).inserted_id)
    try:
        sheet, page = next((s, p) for s, p in name_sheets(db, job_id, [OTTAWA]) if s["sheet_no"] == "A001")
        parse_sheet(db, job_id, sheet, page)
        conditions = {c["type_tag"]: c for c in db.conditions.find({"job_id": job_id})}
        reviews = list(db.review_queue.find({"source": "extract.partition", "doc.job_id": job_id}))
        with capsys.disabled():
            print()
            for tag, c in sorted(conditions.items()):
                print(f"  {c['name']}  studs={c['studs']} layers={c['layers_each_side']} sides={c.get('sides')} "
                      f"fire={c.get('fire_rating')} ul={c.get('ul_assembly')} stc={c.get('stc')}")
            for r in reviews:
                print(f"  review {r['doc'].get('type_tag')}: {r['reason'][:160]}")
        assert {"P2", "P3"} <= set(conditions)
        for tag, spacing in (("P2", 16), ("P3", 24)):
            c = conditions[tag]
            assert c["studs"]["size_in"] == 3.625 and c["studs"]["spacing_in"] == spacing, c
            assert c["layers_each_side"] == [{"thickness_in": 0.625, "board_type": "type_x"}] and c["sides"] == 2, c
        assert len(conditions) + len(reviews) == 4  # P2 to P5; P1 is "not used"
    finally:
        forget_job(db, job_id)
