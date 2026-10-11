"""End-to-end smoke: one Jev choice call and one run_task("parse_partition"), both writing usage rows.

    DB_NAME=demo uv run python -m scripts.smoke
"""
from app import jev
from app.db import get_db, now
from app.router import run_task
from contracts.schemas import SHEET_KINDS

TITLE_BLOCK = "SAMPLE CLINIC FIT-OUT  SHEET A-601  PARTITION TYPES AND DETAILS  SCALE 1-1/2\" = 1'-0\""
PARTITION_BLOCK = """A1
3-5/8" 20 GA METAL STUDS @ 16" O.C.
(1) LAYER 5/8" TYPE 'X' GYPSUM BOARD EACH SIDE
3-1/2" SOUND ATTENUATION BATT INSULATION
1 HOUR RATED - UL U419 - STC 50
EXTEND TO 10'-0" A.F.F."""


def main() -> None:
    db = get_db()
    started = now()
    job_id = str(db.jobs.insert_one({"name": "smoke", "created_at": started, "status": "ready"}).inserted_id)
    print(f"db={db.name} job={job_id}")

    r = jev.choice(TITLE_BLOCK, SHEET_KINDS, "Which kind of drawing sheet is this, from its title block?", job_id=job_id)
    print(f"jev.choice -> {r['choice']} agreed={r['agreed']} model={r['model']} ms={r['ms']} usage={r['usage']}")
    assert r["answers"][0].get("choice") and r["answers"][1].get("choice"), "Jev returned no choice"

    t = run_task("parse_partition", PARTITION_BLOCK, "conditions", job_id=job_id)
    print(f"run_task -> model={t['model']} escalated={t['escalated']}\n  {t['result']}")

    rows = list(db.usage.find({"job_id": job_id, "at": {"$gte": started}}))
    tasks = {u["task"] for u in rows if u.get("ok")}
    print(f"usage rows: {len(rows)} ({sum(1 for u in rows if u.get('ok'))} ok) tasks={sorted(tasks)} "
          f"cost=${sum(u['cost_usd'] for u in rows):.5f}")
    assert {"jev.choice", "parse_partition"} <= tasks, "missing usage rows"
    print("SMOKE OK")


if __name__ == "__main__":
    main()
