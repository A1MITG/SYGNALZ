"""Headline and teaser translation to English with Claude Haiku (2026-10-26).

    translate_stories(stories) -> {url: (english title, english summary)}

Called by app/intelligence/language.py for the non-English stories that
passed the native-language screen: a few dozen a build. One request per 40
stories, structured JSON output, so a reply always parses. Needs the
ANTHROPIC_API_KEY Actions secret; without it nothing is translated and those
stories are left out (logged), which keeps the build running.
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-5-5"
BATCH = 40
_SUMMARY_CHARS = 400

_SYSTEM = (
    "You translate news headlines and teasers into English for a business news "
    "briefing. Keep company, regulator and product names as they are; convert "
    "currency amounts as written (R$ 2 bilhões -> R$2 billion). Write the headline "
    "the way an English financial paper would. Return one item per input id."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                },
                "required": ["id", "title", "summary"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


def _client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package missing: non-English stories are not translated")
        return None
    return anthropic.Anthropic(timeout=60.0, max_retries=2)


def _translate_batch(client, batch):
    payload = [{"id": i, "language": s.get("language"), "title": s.get("title", ""),
                "summary": (s.get("summary") or "")[:_SUMMARY_CHARS]}
               for i, s in enumerate(batch)]
    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=_SYSTEM,
        output_config={"effort": "low",
                       "format": {"type": "json_schema", "schema": _SCHEMA}},
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        logger.warning("Translation stopped early (%s)", response.stop_reason)
        return {}
    text = next((b.text for b in response.content if b.type == "text"), "")
    items = json.loads(text).get("items", [])
    return {batch[it["id"]]["url"]: (it["title"].strip(), it["summary"].strip())
            for it in items if 0 <= it.get("id", -1) < len(batch)}


def translate_stories(stories):
    """English (title, summary) per story URL; {} when translation is off."""
    client = _client()
    if client is None:
        logger.info("ANTHROPIC_API_KEY not set: %d non-English stories left untranslated",
                    len(stories))
        return {}
    import anthropic
    out = {}
    for start in range(0, len(stories), BATCH):
        batch = stories[start:start + BATCH]
        try:
            out.update(_translate_batch(client, batch))
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            logger.warning("Translation request failed: %s", e)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning("Translation reply unreadable: %s", e)
    return out
