"""Live: the synthetic set through real Jev and DeepSeek into lane1. Skipped unless LITELLM_API_KEY is set:

    $env:DB_NAME = "lane1"; infisical run --env=dev -- uv run pytest tests/extract -q
"""
import os

import pytest

from app.db import now, oid
from extract.accuracy import report, score
from extract.ingest import ingest
from tests.extract.helpers import forget_job

pytestmark = pytest.mark.skipif(not os.environ.get("LITELLM_API_KEY"), reason="needs LITELLM_API_KEY (run under infisical run)")

SYNTHETIC = [  # what scripts/make_drawing_set.py draws on A-501
    {"type_tag": tag, "studs": {"size_in": 3.625, "gauge": 20, "spacing_in": 16},
     "layers_each_side": [{"thickness_in": 0.625, "board_type": "type_x"}], "fire_rating": None}
    for tag in ("A1", "A2", "B1")
]


def test_synthetic_set_into_lane1(live_db, synthetic_pdf, tmp_path, capsys):
    db = live_db
    job_id = str(db.jobs.insert_one({"name": "live extract test", "created_at": now(), "status": "uploading"}).inserted_id)
    try:
        ingest(db, job_id, [synthetic_pdf], tmp_path)
        job = db.jobs.find_one({"_id": oid(job_id)})
        assert job["status"] == "ready", job.get("error")

        conditions = list(db.conditions.find({"job_id": job_id}).sort("type_tag", 1))
        reviews = list(db.review_queue.find({"doc.job_id": job_id}))
        with capsys.disabled():
            sheets = db.sheets.find({"job_id": job_id}).sort("page", 1)
            print("\n" + "\n".join(f"  {s['sheet_no']:<6} {s['kind']:<18} {s['routing'].get('method')}" for s in sheets))
            print(report(score(SYNTHETIC, conditions)))
            for r in reviews:
                print(f"  review {r['source']}: {r['reason'][:160]}")
        assert len(conditions) == 3, [r["reason"] for r in reviews]
        assert all(c["studs"]["size_in"] == 3.625 and c["studs"]["gauge"] == 20 for c in conditions)
        assert sorted(c["type_tag"] for c in conditions) == ["A1", "A2", "B1"]
        assert db.rooms.count_documents({"job_id": job_id}) == 6  # A-101 and A-102, 3 rooms each
    finally:
        forget_job(db, job_id)
