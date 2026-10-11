"""Thin client for TypeSafe System One (Jev) through OpenRouter. Jev only picks; it never writes.

    r = choice(state, ["partition_types", "floor_plan", ...], "Which sheet kind is this?", job_id=job_id)
    r -> {"choice": "partition_types" | None, "agreed": bool, "probabilities": {...},
          "model": str, "ms": float, "usage": {"input_tokens", "output_tokens", "cost_usd"}, "answers": [a1, a2]}
    Store r as the doc's `routing`. choice is None when the two asks disagree -> review_queue.

    r = noul(state, "Does this product fail the spec for thickness_in: 0.625?")
    r -> {"p_yes": 0.0-1.0, "model", "ms", "usage"}

Every HTTP call writes one usage row. When Jev fails, the same request goes to the fallback models in
order; if every model fails, JevError is raised and the caller falls back to plain rules.
"""
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from app.db import get_db, log_usage
from app.settings import KEYS_PATH, get_settings

MODEL = "typesafe/jev-1.13"
FALLBACKS = ["cloudflare/clef-flash", "jaredpalmer/kev-4b", "inception/mercury-decide:free"]
TIMEOUT_S = 30.0


class JevError(RuntimeError):
    pass


def _jsonable(value):
    return json.loads(json.dumps(value, default=str))


def _system_one(state, question: dict, *, task: str, job_id, db) -> tuple[dict, str, float, dict]:
    """POST one question; returns (answer, model, ms, usage). Tries Jev, then each fallback."""
    s = get_settings()
    if not s.openrouter_api_key:
        raise JevError(f"OPENROUTER_API_KEY is not set (env or {KEYS_PATH})")
    url = s.openrouter_base_url.rstrip("/") + "/v1/systemone"
    headers = {"Authorization": f"Bearer {s.openrouter_api_key}"}
    failures = []
    for model in [MODEL, *FALLBACKS]:
        body = {"model": model, "state": _jsonable(state), "questions": {"q": question}}
        t0 = time.perf_counter()
        try:
            resp = httpx.post(url, json=body, headers=headers, timeout=TIMEOUT_S)
            resp.raise_for_status()
            data = resp.json()
            answer = data["answers"]["q"]
        except Exception as e:  # HTTP error, timeout, or an unexpected body
            detail = getattr(getattr(e, "response", None), "text", "") or str(e)
            log_usage(db, task=task, model=model, ms=(time.perf_counter() - t0) * 1000, job_id=job_id, ok=False, error=detail)
            failures.append(f"{model}: {detail[:200]}")
            continue
        ms = (time.perf_counter() - t0) * 1000
        u = data.get("usage") or {}
        usage = {
            "input_tokens": u.get("input_tokens", 0),
            "output_tokens": u.get("output_tokens", 0),
            "cost_usd": float(u.get("cost") or 0.0),
        }
        log_usage(db, task=task, model=model, ms=ms, job_id=job_id, **usage)
        return answer, model, ms, usage
    raise JevError("all System One models failed: " + " | ".join(failures))


def _sum_usage(*usages: dict) -> dict:
    return {k: sum(u[k] for u in usages) for k in ("input_tokens", "output_tokens", "cost_usd")}


def choice(
    state,
    options,
    question: str = "Which option fits best?",
    *,
    task: str = "jev.choice",
    job_id=None,
    db=None,
) -> dict:
    """Ask twice, the second time with the option order reversed (Jev favors the first option), and accept
    only when both agree. `options` is a list of names or a {name: description | None} dict."""
    db = db if db is not None else get_db()
    criteria = dict(options) if isinstance(options, dict) else {o: None for o in options}
    if len(criteria) < 2:
        raise ValueError("choice needs at least two options")
    order = list(criteria)
    random.shuffle(order)

    def ask(keys):
        q = {"type": "choice", "instructions": question, "criteria": {k: criteria[k] for k in keys}}
        return _system_one(state, q, task=task, job_id=job_id, db=db)

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        (a1, m1, _, u1), (a2, m2, _, u2) = pool.map(ask, [order, order[::-1]])
    ms = (time.perf_counter() - t0) * 1000
    agreed = a1.get("choice") == a2.get("choice")
    p1, p2 = a1.get("probabilities") or {}, a2.get("probabilities") or {}
    return {
        "choice": a1.get("choice") if agreed else None,
        "agreed": agreed,
        "probabilities": {k: round((p1.get(k, 0) + p2.get(k, 0)) / 2, 4) for k in criteria},
        "model": m1 if m1 == m2 else f"{m1}+{m2}",
        "ms": round(ms, 1),
        "usage": _sum_usage(u1, u2),
        "answers": [a1, a2],
    }


def noul(state, question: str, *, criteria: dict | None = None, task: str = "jev.noul", job_id=None, db=None) -> dict:
    """Yes/no question. p_yes near 1 means yes; the spec check flags when p_yes > 0.7."""
    db = db if db is not None else get_db()
    q = {"type": "noul", "instructions": question}
    if criteria:
        q["criteria"] = criteria
    answer, model, ms, usage = _system_one(state, q, task=task, job_id=job_id, db=db)
    return {"p_yes": float(answer.get("noul", 0.0)), "model": model, "ms": round(ms, 1), "usage": usage}
