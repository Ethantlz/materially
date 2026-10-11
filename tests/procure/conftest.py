import pytest


@pytest.fixture(autouse=True)
def _test_find_runs_one_material_at_a_time(request, monkeypatch):
    """test_find.py was written for the one-at-a-time /procure: it asserts the order of the Firecrawl calls and exactly
    how many were made before a block. Pinning PULL_AT_ONCE to 1 keeps those assertions meaningful and deterministic;
    test_parallel_pull.py runs the real default."""
    if request.module.__name__.rsplit(".", 1)[-1] == "test_find":
        monkeypatch.setattr("procure.api.PULL_AT_ONCE", 1)
