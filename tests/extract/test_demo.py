"""The demo set (WCU H.F. Robinson Admin Building Suite 420): A003 PARTITION TYPES - METAL STUD.

fixtures/demo.pdf is a public bid set, gitignored; these tests skip when it isn't on disk. Five wall families
(SA, SB, SC, SD, SH), each with a schedule whose columns are the wall types. SB2's stud size reads
"SEE UL DET.", which the conditions contract rejects: that is the video's "bad parse goes to review" beat.
"""
import os

import pytest

from app.db import now
from app.settings import ROOT
from extract.analyze import parse_sheet
from extract.ingest import name_sheets, read_pages
from extract.partitions import partition_blocks
from extract.sheetinfo import common_title_block_text, locate_title_blocks
from tests.extract.helpers import forget_job

DEMO = ROOT / "fixtures" / "demo.pdf"
pytestmark = pytest.mark.skipif(not DEMO.exists(), reason=f"{DEMO} not on disk")
COLUMNS = ["SA4", "SB2", "SC2", "SC4", "SD4", "SH2", "SH4", "SH6"]


@pytest.fixture(scope="module")
def a003():
    pages = read_pages(DEMO)
    locate_title_blocks(pages, common_title_block_text(pages))
    return pages[7]


def test_a003_blocks_are_the_schedule_columns(a003):
    blocks = {b.tag: b.text for b in partition_blocks(a003)}
    assert sorted(blocks) == COLUMNS
    sa4 = blocks["SA4"]
    assert 'STUD SIZE: 3 5/8"' in sa4 and "ACTUAL DIMENSION 'X': 5 3/8\"" in sa4 and "U419" in sa4 and "STC" in sa4
    assert "RESILIENT" in sa4 and "TYPE 'X'" in sa4
    assert 'STUD SIZE: 2 1/2"' in blocks["SC2"] and "3 1/8\"" in blocks["SC2"] and '3 5/8"' not in blocks["SC2"].split("SCHEDULE")[-1]
    assert 'STUD SIZE: 3 5/8"' in blocks["SC4"] and "4 1/4\"" in blocks["SC4"]
    assert "4 7/8\"" in blocks["SD4"]
    assert "STUD SIZE: SEE UL DET." in blocks["SB2"] and "2 HR" in blocks["SB2"]
    for tag, size, x in (("SH2", '2 1/2"', '3 1/8"'), ("SH4", '4"', '4 5/8"'), ("SH6", '6"', '6 5/8"')):
        assert f"STUD SIZE: {size}" in blocks[tag] and x in blocks[tag] and "U499" in blocks[tag], blocks[tag]
        assert "SHAFT LINER" in blocks[tag] and "CORRIDOR SIDE" in blocks[tag]
    assert not any("GENERAL PARTITION NOTES" in t or "WALL TAG LEGEND" in t for t in blocks.values())


@pytest.mark.skipif(not os.environ.get("LITELLM_API_KEY"), reason="needs LITELLM_API_KEY (run under infisical run)")
def test_a003_conditions_and_the_rejected_sb2(live_db, capsys):
    db = live_db
    job_id = str(db.jobs.insert_one({"name": "demo A003 test", "created_at": now(), "status": "processing"}).inserted_id)
    try:
        sheet, page = next((s, p) for s, p in name_sheets(db, job_id, [DEMO]) if s["sheet_no"] == "A003")
        assert sheet["kind"] == "partition_types"
        parse_sheet(db, job_id, sheet, page)
        conditions = list(db.conditions.find({"job_id": job_id}).sort("type_tag", 1))
        reviews = list(db.review_queue.find({"source": "extract.partition", "doc.job_id": job_id}))
        with capsys.disabled():
            print()
            for c in conditions:
                print(f"  {c['type_tag']:<4} {c['studs']} {c['layers_each_side']} sides={c.get('sides')} "
                      f"fire={c.get('fire_rating')} ul={c.get('ul_assembly')} stc={c.get('stc')} {c['routing']}")
            for r in reviews:
                print(f"  review {r['doc'].get('type_tag')}: {r['reason'][:200]}")
        assert [c["type_tag"] for c in conditions] == ["SA4", "SC2", "SC4", "SD4", "SH2", "SH4", "SH6"]
        assert [r["doc"]["type_tag"] for r in reviews] == ["SB2"]
        sizes = {c["type_tag"]: c["studs"]["size_in"] for c in conditions}
        assert sizes == {"SA4": 3.625, "SC2": 2.5, "SC4": 3.625, "SD4": 3.625, "SH2": 2.5, "SH4": 4, "SH6": 6}
    finally:
        forget_job(db, job_id)
