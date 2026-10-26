"""The swappable LLM adapter (app/llm.py) and translation through it."""
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from app import llm
from app.scraper import translate

_ENV_KEYS = ("SIGNAL_LLM_PROVIDER", "SIGNAL_LLM_MODEL", "SIGNAL_LLM_EFFORT",
             "SIGNAL_TRANSLATE_PROVIDER", "SIGNAL_TRANSLATE_MODEL", "SIGNAL_TRANSLATE_EFFORT",
             "ANTHROPIC_API_KEY", "OPENAI_API_KEY")


def _env(**values):
    clean = {k: "" for k in _ENV_KEYS}
    clean.update(values)
    return mock.patch.dict("os.environ", clean)


class SettingsTest(unittest.TestCase):
    def test_default_is_the_configured_cheap_model(self):
        with _env():
            cfg = llm.settings("translate")
        self.assertEqual(cfg["provider"], "anthropic")
        self.assertEqual(cfg["model"], "claude-haiku-5-5")
        self.assertEqual(cfg["key_env"], "ANTHROPIC_API_KEY")

    def test_general_override_swaps_provider_and_model(self):
        with _env(SIGNAL_LLM_PROVIDER="openai", SIGNAL_LLM_MODEL="some-mini"):
            cfg = llm.settings("translate")
        self.assertEqual((cfg["provider"], cfg["model"], cfg["key_env"]),
                         ("openai", "some-mini", "OPENAI_API_KEY"))

    def test_switching_provider_drops_the_old_providers_effort(self):
        with _env(SIGNAL_LLM_PROVIDER="openai", SIGNAL_LLM_MODEL="m"):
            self.assertNotIn("effort", llm.settings("translate"))

    def test_task_override_beats_general_override(self):
        with _env(SIGNAL_LLM_MODEL="general", SIGNAL_TRANSLATE_MODEL="task"):
            self.assertEqual(llm.settings("translate")["model"], "task")

    def test_unknown_task_is_off(self):
        with _env(ANTHROPIC_API_KEY="k"):
            self.assertIn("off", llm.available("no-such-task"))

    def test_missing_key_is_reported(self):
        with _env():
            self.assertEqual(llm.available("translate"), "ANTHROPIC_API_KEY not set")
        with _env(ANTHROPIC_API_KEY="k"):
            self.assertEqual(llm.available("translate"), "")

    def test_provider_none_turns_the_task_off(self):
        with _env(SIGNAL_LLM_PROVIDER="none", ANTHROPIC_API_KEY="k"):
            self.assertIsNone(llm.complete_json("translate", "s", "u", {}))

    def test_no_model_id_in_feature_code(self):
        import pathlib
        root = pathlib.Path(llm.__file__).parent
        for path in root.rglob("*.py"):
            if path.name == "llm.py":
                continue
            text = path.read_text(encoding="utf-8")
            for marker in ("claude-haiku", "claude-sonnet", "claude-opus", "gpt-", "import anthropic", "import openai"):
                self.assertNotIn(marker, text, f"{path} names a model or SDK directly")


class ProviderTest(unittest.TestCase):
    def test_anthropic_call_uses_configured_model(self):
        sent = {}

        class FakeMessages:
            def create(self, **kw):
                sent.update(kw)
                return SimpleNamespace(stop_reason="end_turn",
                                       content=[SimpleNamespace(type="text", text='{"items": []}')])

        fake = SimpleNamespace(Anthropic=lambda **kw: SimpleNamespace(messages=FakeMessages()),
                               APIStatusError=RuntimeError, APIConnectionError=RuntimeError)
        with _env(ANTHROPIC_API_KEY="k", SIGNAL_LLM_MODEL="claude-other"), \
                mock.patch.dict("sys.modules", {"anthropic": fake}):
            self.assertEqual(llm.complete_json("translate", "sys", "user", {"type": "object"}), {"items": []})
        self.assertEqual(sent["model"], "claude-other")
        self.assertEqual(sent["output_config"]["effort"], "low")

    def test_openai_call_posts_schema_and_key(self):
        response = mock.Mock()
        response.json.return_value = {"choices": [{"message": {"content": '{"items": []}'}}]}
        with _env(OPENAI_API_KEY="ok", SIGNAL_LLM_PROVIDER="openai", SIGNAL_LLM_MODEL="m",
                  SIGNAL_LLM_EFFORT=""), \
                mock.patch.object(llm.requests, "post", return_value=response) as post:
            self.assertEqual(llm.complete_json("translate", "sys", "user", {"type": "object"}), {"items": []})
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["model"], "m")
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer ok")

    def test_failed_request_returns_none(self):
        with _env(OPENAI_API_KEY="ok", SIGNAL_LLM_PROVIDER="openai", SIGNAL_LLM_MODEL="m"), \
                mock.patch.object(llm.requests, "post", side_effect=llm.requests.ConnectionError("x")):
            self.assertIsNone(llm.complete_json("translate", "s", "u", {}))


class TranslateTest(unittest.TestCase):
    stories = [{"url": "https://a/1", "title": "Seguradora lucra", "language": "pt"},
               {"url": "https://a/2", "title": "保険会社", "language": "ja"}]

    def test_without_key_nothing_is_translated(self):
        with _env(), mock.patch.object(llm, "complete_json") as call:
            self.assertEqual(translate.translate_stories(self.stories), {})
        call.assert_not_called()

    def test_reply_maps_back_to_urls(self):
        reply = {"items": [{"id": 1, "title": " Insurer ", "summary": "s"},
                           {"id": 9, "title": "stray", "summary": ""}]}
        with _env(ANTHROPIC_API_KEY="k"), mock.patch.object(llm, "complete_json", return_value=reply) as call:
            out = translate.translate_stories(self.stories)
        self.assertEqual(out, {"https://a/2": ("Insurer", "s")})
        self.assertEqual(call.call_args.args[0], "translate")
        self.assertEqual(len(json.loads(call.call_args.args[2])), 2)


if __name__ == "__main__":
    unittest.main()
