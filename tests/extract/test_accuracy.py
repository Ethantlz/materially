"""Partition accuracy, field by field against hand-labelled sets (SPEC section 10).

The live report runs for each fixtures/expected/<set>.json whose PDF is on disk (PDFs are gitignored), and
needs LITELLM_API_KEY; the scorer itself is tested offline.
"""
import json
import os

import pytest

from app.settings import ROOT
from extract.accuracy import FIELDS, report, score
from extract.analyze import parse_block
from extract.ingest import read_pages, sheet_docs
from extract.partitions import partition_blocks

A1 = {"type_tag": "A1", "studs": {"size_in": 3.625, "gauge": 20, "spacing_in": 16},
      "layers_each_side": [{"thickness_in": 0.625, "board_type": "type_x"}], "fire_rating": 1}


def test_score_by_field():
    expected = [A1, {**A1, "type_tag": "B2"}]
    got = [{**A1, "studs": {**A1["studs"], "gauge": 25.0}, "fire_rating": 1.0}]
    s = score(expected, got)
    assert s["A1"] == {f: f != "gauge" for f in FIELDS}
    assert s["B2"] == {f: False for f in FIELDS}, "a tag that was never parsed gets every field wrong"
    text = report(s)
    assert "A1" in text and "B2" in text and "5/12" in text  # 5 of 12 fields right


def test_layers_compare_the_whole_stack():
    two = {**A1, "layers_each_side": [{"thickness_in": 0.625, "board_type": "type_x"}] * 2}
    s = score([two], [A1])["A1"]
    assert not s["layers"] and not s["board type"]


def _labelled_sets():
    for path in sorted((ROOT / "fixtures" / "expected").glob("*.json")):
        if path.stem.endswith("_sheets"):
            continue
        labels = json.loads(path.read_text())
        pdf = ROOT / labels.get("pdf", f"fixtures/{path.stem}.pdf")
        yield pytest.param(labels, pdf, id=path.stem, marks=pytest.mark.skipif(not pdf.exists(), reason=f"{pdf} not on disk"))


@pytest.mark.skipif(not os.environ.get("LITELLM_API_KEY"), reason="needs LITELLM_API_KEY (run under infisical run)")
@pytest.mark.parametrize("labels,pdf", list(_labelled_sets()) or [pytest.param(None, None, marks=pytest.mark.skip("no labelled sets"))])
def test_partition_accuracy_report(labels, pdf, mock_db, capsys):
    pages = read_pages(pdf)
    docs = sheet_docs("accuracy", [(pdf.name, pages)])
    partition = [(d, p) for d, p in zip(docs, pages) if d["kind"] == "partition_types"]
    partition = partition or [(d, p) for d, p in zip(docs, pages) if partition_blocks(p)]
    for doc, page in partition:
        doc["_id"] = mock_db.sheets.insert_one(doc).inserted_id
        for block in partition_blocks(page):
            parse_block(mock_db, "accuracy", doc, block)
    got = list(mock_db.conditions.find({"job_id": "accuracy"}))
    failed = list(mock_db.review_queue.find({"source": "extract.partition"}))
    with capsys.disabled():
        print(f"\n{labels.get('set')}: {len(partition)} partition sheet(s), {len(got)} conditions, {len(failed)} to review")
        print(report(score(labels["conditions"], got)))  # a report, not a pass/fail gate
