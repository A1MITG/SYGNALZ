"""Headline and teaser translation to English (2026-10-26).

    translate_stories(stories) -> {url: (english title, english summary)}

Called by app/intelligence/language.py for the non-English stories that
passed the native-language screen: a few dozen a build. One request per 40
stories, structured JSON output, so a reply always parses.

The model is not chosen here: app/llm.py runs the "translate" task with the
provider and model in config/llm.yaml (or the SIGNAL_TRANSLATE_* /
SIGNAL_LLM_* overrides). Without that provider's key nothing is translated
and those stories are left out (logged), which keeps the build running.
"""
import json
import logging

from .. import llm

logger = logging.getLogger(__name__)

TASK = "translate"
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


def _translate_batch(batch):
    payload = [{"id": i, "language": s.get("language"), "title": s.get("title", ""),
                "summary": (s.get("summary") or "")[:_SUMMARY_CHARS]}
               for i, s in enumerate(batch)]
    reply = llm.complete_json(TASK, _SYSTEM, json.dumps(payload, ensure_ascii=False), _SCHEMA)
    if not reply:
        return {}
    out = {}
    for it in reply.get("items") or []:
        try:
            i = int(it["id"])
            if 0 <= i < len(batch):
                out[batch[i]["url"]] = (it["title"].strip(), it["summary"].strip())
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return out


def translate_stories(stories):
    """English (title, summary) per story URL; {} when translation is off."""
    if not stories:
        return {}
    reason = llm.available(TASK)
    if reason:
        logger.info("Translation off (%s): %d non-English stories left out", reason, len(stories))
        return {}
    out = {}
    for start in range(0, len(stories), BATCH):
        out.update(_translate_batch(stories[start:start + BATCH]))
    logger.info("Translated %d of %d non-English stories with %s", len(out), len(stories),
                llm.settings(TASK).get("model"))
    return out
