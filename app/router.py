"""run_task: the one door for every model call that reads or writes (SPEC §6).

    out = run_task("parse_partition", block_text, "conditions", job_id=job_id)
    out -> {"result": {...model JSON...}, "model": str, "escalated": bool}

1. model_policy lookup for the task (read live, so an admin edit takes effect on the next call).
2. Call policy.model (DeepSeek: Luminal directly when LUMINAL_* is set, else through the LiteLLM proxy).
3. Parse the JSON and validate it against contracts schema `validator_name` (caller fields not required).
4. On any failure, retry once on policy.retry_model (Sonnet) with the rejection reason; escalated = True.
5. One usage row per call. If the retry fails too, TaskFailed is raised: send e.reason to review_queue.
"""
import json
import re
import time
from functools import lru_cache

from app import prompts
from app.db import get_db, log_usage
from app.settings import KEYS_PATH, get_settings
from contracts.validate import ai_schema, validate

# USD per 1M tokens (input, output). DeepSeek is an estimate until the Luminal dashboard price goes here.
PRICES = {
    "deepseek-ai/DeepSeek-V4.1-Flash": (0.10, 0.40),
    "claude-sonnet-5-5": (2.00, 10.00),
}


class TaskFailed(RuntimeError):
    def __init__(self, task: str, reason: str, raw: str | None = None):
        super().__init__(f"{task}: {reason}")
        self.task, self.reason, self.raw = task, reason, raw


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    p_in, p_out = PRICES.get(model.removeprefix("openrouter/"), (0.0, 0.0))
    return (input_tokens * p_in + output_tokens * p_out) / 1_000_000


@lru_cache(maxsize=4)
def _openai(base_url: str, api_key: str):
    from openai import OpenAI

    return OpenAI(base_url=base_url, api_key=api_key, timeout=90, max_retries=1)


@lru_cache(maxsize=1)
def _anthropic(api_key: str):
    import anthropic

    return anthropic.Anthropic(api_key=api_key or None, timeout=120, max_retries=1)


def _chat_client(model: str):
    """(client, model name) for an OpenAI-compatible model."""
    s = get_settings()
    if model.startswith("openrouter/"):
        return _openai(s.openrouter_base_url.rstrip("/") + "/v1", s.openrouter_api_key), model.removeprefix("openrouter/")
    if s.luminal_base_url and s.luminal_api_key:
        return _openai(s.luminal_base_url, s.luminal_api_key), model
    if s.litellm_api_key:
        return _openai(s.litellm_base_url, s.litellm_api_key), model
    raise RuntimeError(f"set LUMINAL_BASE_URL + LUMINAL_API_KEY, or LITELLM_API_KEY (env or {KEYS_PATH})")


def call_model(model: str, system: str, user: str) -> tuple[str, int, int]:
    """Returns (text, input_tokens, output_tokens). claude-* -> Anthropic API; anything else is
    OpenAI-compatible (openrouter/<id> -> OpenRouter, else Luminal or LiteLLM)."""
    if model.startswith("claude-"):
        resp = _anthropic(get_settings().anthropic_api_key).beta.messages.create(
            model=model,
            max_tokens=8000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": "low"},
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"{model} refused: {resp.stop_details}")
        text = "".join(b.text for b in resp.content if b.type == "text")
        return text, resp.usage.input_tokens, resp.usage.output_tokens
    client, name = _chat_client(model)
    resp = client.chat.completions.create(
        model=name,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0,
        max_tokens=4000,
    )
    usage = resp.usage
    return resp.choices[0].message.content or "", getattr(usage, "prompt_tokens", 0), getattr(usage, "completion_tokens", 0)


def parse_json(text: str):
    """The first JSON object or array in a model reply, ignoring <think> blocks and ``` fences."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise ValueError(f"no JSON in reply: {text[:120]!r}")
    obj, _ = json.JSONDecoder().raw_decode(text[min(starts):])
    return obj


def system_prompt(task: str, policy: dict, validator_name: str | None, system: str | None = None) -> str:
    body = system or policy.get("prompt") or prompts.PROMPTS.get(task, f"Do the task '{task}' on the input.")
    out = f"{prompts.COMMON}\n\n{body}"
    if validator_name:
        out += "\n\nThe JSON must satisfy this MongoDB $jsonSchema:\n" + json.dumps(ai_schema(validator_name))
    return out


def _attempt(db, task, model, system, user, validator_name, job_id):
    """One call. Returns (result, None, raw) on success or (None, reason, raw)."""
    t0 = time.perf_counter()
    try:
        text, tin, tout = call_model(model, system, user)
    except Exception as e:
        log_usage(db, task=task, model=model, ms=(time.perf_counter() - t0) * 1000, job_id=job_id, ok=False, error=str(e))
        return None, f"{model} call failed: {e}", None
    ms = (time.perf_counter() - t0) * 1000
    result = reason = None
    try:
        result = parse_json(text)
        if validator_name:
            errs = validate(validator_name, result, ai=True)
            if errs:
                reason = "; ".join(errs[:8])
    except ValueError as e:
        reason = f"unparseable JSON: {e}"
    log_usage(
        db, task=task, model=model, input_tokens=tin, output_tokens=tout, cost_usd=cost_usd(model, tin, tout),
        ms=ms, job_id=job_id, ok=reason is None, error=reason,
    )
    return (result, None, text) if reason is None else (None, reason, text)


def run_task(task: str, payload, validator_name: str | None = None, *, job_id=None, system: str | None = None, db=None) -> dict:
    db = db if db is not None else get_db()
    policy = db.model_policy.find_one({"task": task})
    if not policy or not policy.get("enabled", False):
        raise TaskFailed(task, "no enabled model_policy row for this task")
    if job_id is None and isinstance(payload, dict):
        job_id = payload.get("job_id")
    sys_prompt = system_prompt(task, policy, validator_name, system)
    user = payload if isinstance(payload, str) else json.dumps(payload, default=str, indent=1)

    attempts = [(policy["model"], False)]
    if policy.get("retry_model"):
        attempts.append((policy["retry_model"], True))
    reason = raw = None
    for model, escalated in attempts:
        prompt = user if reason is None else f"{user}\n\nA previous answer was rejected: {reason}\nReturn corrected JSON only."
        result, reason, raw = _attempt(db, task, model, sys_prompt, prompt, validator_name, job_id)
        if reason is None:
            return {"result": result, "model": model, "escalated": escalated}
    raise TaskFailed(task, reason, raw)
