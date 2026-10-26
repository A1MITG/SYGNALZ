"""One door to every language model the pipeline uses (2026-10-26).

    settings(task)                          -> {'provider', 'model', 'effort', ...}
    complete_json(task, system, user, schema) -> dict, or None when the task is
                                               off (no key, provider 'none') or
                                               the call failed

Feature code names a task ("translate"), never a model or a provider. The
provider and model come from config/llm.yaml, and an environment variable
overrides either for one build (SIGNAL_<TASK>_MODEL, then SIGNAL_LLM_MODEL;
the same for _PROVIDER and _EFFORT). So switching to another model, or to
OpenAI, is a setting, not a code change.

Providers: `anthropic` (the Anthropic SDK, structured JSON output) and
`openai` (its chat completions endpoint with a JSON schema response).
"""
import json
import logging
import os
import pathlib
from functools import lru_cache

import requests
import yaml

logger = logging.getLogger(__name__)

CONFIG = pathlib.Path(__file__).resolve().parents[1] / "config" / "llm.yaml"
TIMEOUT = 60


@lru_cache(maxsize=1)
def _config():
    if not CONFIG.exists():
        return {}
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}


def _env(task, field):
    return (os.environ.get(f"SIGNAL_{task.upper()}_{field}")
            or os.environ.get(f"SIGNAL_LLM_{field}") or None)


def settings(task):
    """The task's provider, model and effort, with environment overrides, and
    its provider's own settings (key_env, url)."""
    base = dict((_config().get("tasks") or {}).get(task) or {})
    for field in ("provider", "model", "effort"):
        value = _env(task, field.upper())
        if value:
            base[field] = value
    provider = (base.get("provider") or "none").lower()
    base["provider"] = provider
    base.update({k: v for k, v in ((_config().get("providers") or {}).get(provider) or {}).items()
                 if k not in base})
    return base


def _anthropic(cfg, key, system, user, schema):
    import anthropic
    client = anthropic.Anthropic(api_key=key, timeout=TIMEOUT, max_retries=2)
    output_config = {"format": {"type": "json_schema", "schema": schema}}
    if cfg.get("effort"):
        output_config["effort"] = cfg["effort"]
    try:
        response = client.messages.create(
            model=cfg["model"], max_tokens=16000, system=system,
            output_config=output_config,
            messages=[{"role": "user", "content": user}])
    except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
        logger.warning("%s request failed: %s", cfg["model"], e)
        return None
    if response.stop_reason in ("refusal", "max_tokens"):
        logger.warning("%s stopped early (%s)", cfg["model"], response.stop_reason)
        return None
    return next((b.text for b in response.content if b.type == "text"), None)


def _openai(cfg, key, system, user, schema):
    body = {
        "model": cfg["model"],
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "result", "strict": True, "schema": schema}},
    }
    if cfg.get("effort"):
        body["reasoning_effort"] = cfg["effort"]
    try:
        r = requests.post(cfg.get("url", "https://api.openai.com/v1/chat/completions"),
                          headers={"Authorization": f"Bearer {key}"}, json=body, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        logger.warning("%s request failed: %s", cfg["model"], e)
        return None


_PROVIDERS = {"anthropic": _anthropic, "openai": _openai}


def available(task):
    """Why the task can't run ('' when it can): no provider/model, or no key."""
    cfg = settings(task)
    if cfg["provider"] not in _PROVIDERS or not cfg.get("model"):
        return f"provider {cfg['provider']!r} is off"
    if not os.environ.get(cfg.get("key_env") or ""):
        return f"{cfg.get('key_env')} not set"
    return ""


def complete_json(task, system, user, schema):
    """The model's reply for this task as a dict matching ``schema``, or None."""
    reason = available(task)
    if reason:
        logger.info("LLM task %r skipped: %s", task, reason)
        return None
    cfg = settings(task)
    text = _PROVIDERS[cfg["provider"]](cfg, os.environ[cfg["key_env"]], system, user, schema)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("%s reply unreadable: %s", cfg["model"], e)
        return None
