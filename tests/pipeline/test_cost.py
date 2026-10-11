"""GET /api/jobs/{job_id}/cost: $group on usage."""
import pytest

from app.db import now
from tests.pipeline.conftest import new_id


def usage(job_id, task, model, cost, ok=True):
    return {"job_id": job_id, "task": task, "model": model, "input_tokens": 100, "output_tokens": 20, "cost_usd": cost, "ms": 5.0, "at": now(), "ok": ok}


def test_cost_endpoint_sums_usage_rows(db, job, client):
    other = new_id()
    try:
        db.usage.insert_many([
            usage(job, "ewo_route", "typesafe/jev-1.13", 0.0004),
            usage(job, "ewo_route", "typesafe/jev-1.13", 0.0004),
            usage(job, "parse_partition", "deepseek-ai/DeepSeek-V4.1-Flash", 0.00023),
            usage(job, "parse_partition", "deepseek-ai/DeepSeek-V4.1-Flash", 0.00031),
            usage(job, "parse_partition", "claude-sonnet-5-5", 0.0121),  # the Sonnet retry is its own row
            usage(job, "price_extra", "deepseek-ai/DeepSeek-V4.1-Flash", 0.0, ok=False),  # a failed call is still a call
            usage(other, "parse_partition", "deepseek-ai/DeepSeek-V4.1-Flash", 5.0),  # another job's
        ])

        r = client.get(f"/api/jobs/{job}/cost")

        assert r.status_code == 200
        body = r.json()
        assert body["calls"] == 6
        assert body["cost_usd"] == pytest.approx(0.0004 * 2 + 0.00023 + 0.00031 + 0.0121)
        by = {(t["task"], t["model"]): t for t in body["by_task"]}
        assert len(body["by_task"]) == 4
        assert by[("ewo_route", "typesafe/jev-1.13")]["calls"] == 2
        assert by[("parse_partition", "deepseek-ai/DeepSeek-V4.1-Flash")]["calls"] == 2
        assert by[("parse_partition", "deepseek-ai/DeepSeek-V4.1-Flash")]["cost_usd"] == pytest.approx(0.00054)
        assert by[("parse_partition", "claude-sonnet-5-5")]["cost_usd"] == pytest.approx(0.0121)
        assert by[("price_extra", "deepseek-ai/DeepSeek-V4.1-Flash")]["calls"] == 1
        assert [t["cost_usd"] for t in body["by_task"]] == sorted((t["cost_usd"] for t in body["by_task"]), reverse=True)
        assert sum(t["calls"] for t in body["by_task"]) == body["calls"]
    finally:
        db.usage.delete_many({"job_id": other})


def test_cost_of_a_job_with_no_calls(db, client):
    assert client.get(f"/api/jobs/{new_id()}/cost").json() == {"calls": 0, "cost_usd": 0.0, "by_task": []}
