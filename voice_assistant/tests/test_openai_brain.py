from __future__ import annotations

import io
import json
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from bmo_voice.config import Settings
from bmo_voice.openai_brain import OpenAIBrainError, OpenAICompatBrain


def _settings(**overrides) -> Settings:
    values = dict(
        vosk_model=Path("/unused"),
        whisper_model=Path("/unused/model.bin"),
        brain_provider="openai",
        openai_base_url="https://endpoint.example/v1",
        openai_model="gpt-5.6-sol",
        openai_api_key="secret-key-should-not-leak",
        openai_timeout_seconds=42,
    )
    values.update(overrides)
    return Settings(**values)


class _FakeResponse:
    def __init__(self, body: str):
        self._body = body.encode("utf-8")

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def _chat_body(content) -> str:
    return json.dumps({"choices": [{"message": {"content": content}}]})


class OpenAIBrainClientTests(unittest.TestCase):
    def test_builds_request_and_parses_reply(self) -> None:
        captured: dict = {}

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["auth"] = request.get_header("Authorization")
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _FakeResponse(_chat_body("你好，我是 BMO。"))

        brain = OpenAICompatBrain(_settings())
        with patch(
            "bmo_voice.openai_brain.urllib.request.urlopen", fake_urlopen
        ):
            reply = brain.ask("你好")

        self.assertEqual(reply, "你好，我是 BMO。")
        self.assertEqual(
            captured["url"], "https://endpoint.example/v1/chat/completions"
        )
        self.assertEqual(captured["auth"], "Bearer secret-key-should-not-leak")
        self.assertEqual(captured["timeout"], 42)
        self.assertEqual(captured["body"]["model"], "gpt-5.6-sol")
        self.assertEqual(captured["body"]["messages"][-1]["role"], "user")
        self.assertEqual(captured["body"]["messages"][-1]["content"], "你好")
        self.assertEqual(captured["body"]["messages"][0]["role"], "system")

    def test_base_url_trailing_slash_is_normalized(self) -> None:
        brain = OpenAICompatBrain(
            _settings(openai_base_url="https://endpoint.example/v1/")
        )
        self.assertEqual(
            brain._url, "https://endpoint.example/v1/chat/completions"
        )

    def test_list_content_parts_are_joined(self) -> None:
        body = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "第一段 "},
                                {"type": "text", "text": "第二段"},
                            ]
                        }
                    }
                ]
            }
        )
        brain = OpenAICompatBrain(_settings())
        with patch(
            "bmo_voice.openai_brain.urllib.request.urlopen",
            lambda *a, **k: _FakeResponse(body),
        ):
            self.assertEqual(brain.ask("x"), "第一段 第二段")

    def test_http_error_raises_and_does_not_leak_key(self) -> None:
        def raise_http(*_a, **_k):
            raise urllib.error.HTTPError(
                "https://endpoint.example/v1/chat/completions",
                500,
                "Server Error",
                hdrs=None,
                fp=io.BytesIO(b'{"error":"boom"}'),
            )

        brain = OpenAICompatBrain(_settings())
        with patch(
            "bmo_voice.openai_brain.urllib.request.urlopen", raise_http
        ):
            with self.assertRaises(OpenAIBrainError) as ctx:
                brain.ask("x")
        message = str(ctx.exception)
        self.assertIn("500", message)
        self.assertNotIn("secret-key-should-not-leak", message)

    def test_unreachable_endpoint_raises(self) -> None:
        def raise_urlerror(*_a, **_k):
            raise urllib.error.URLError("connection refused")

        brain = OpenAICompatBrain(_settings())
        with patch(
            "bmo_voice.openai_brain.urllib.request.urlopen", raise_urlerror
        ):
            with self.assertRaises(OpenAIBrainError):
                brain.ask("x")

    def test_invalid_json_raises(self) -> None:
        brain = OpenAICompatBrain(_settings())
        with patch(
            "bmo_voice.openai_brain.urllib.request.urlopen",
            lambda *a, **k: _FakeResponse("not json"),
        ):
            with self.assertRaises(OpenAIBrainError):
                brain.ask("x")

    def test_missing_message_raises(self) -> None:
        brain = OpenAICompatBrain(_settings())
        with patch(
            "bmo_voice.openai_brain.urllib.request.urlopen",
            lambda *a, **k: _FakeResponse(json.dumps({"choices": []})),
        ):
            with self.assertRaises(OpenAIBrainError):
                brain.ask("x")

    def test_empty_content_raises(self) -> None:
        brain = OpenAICompatBrain(_settings())
        with patch(
            "bmo_voice.openai_brain.urllib.request.urlopen",
            lambda *a, **k: _FakeResponse(_chat_body("   ")),
        ):
            with self.assertRaises(OpenAIBrainError):
                brain.ask("x")


class ConfigValidationTests(unittest.TestCase):
    def test_openai_provider_requires_endpoint_fields(self) -> None:
        settings = Settings.from_env(
            {
                "BMO_VOSK_MODEL": "/unused",
                "BMO_WHISPER_MODEL": "/unused/model.bin",
                "BMO_BRAIN": "openai",
            }
        )
        with self.assertRaises(Exception) as ctx:
            settings.validate_runtime()
        self.assertIn("BMO_OPENAI_BASE_URL", str(ctx.exception))

    def test_invalid_provider_rejected(self) -> None:
        with self.assertRaises(Exception):
            Settings.from_env(
                {
                    "BMO_VOSK_MODEL": "/unused",
                    "BMO_WHISPER_MODEL": "/unused/model.bin",
                    "BMO_BRAIN": "bogus",
                }
            )

    def test_api_key_not_in_repr(self) -> None:
        settings = _settings()
        self.assertNotIn("secret-key-should-not-leak", repr(settings))


if __name__ == "__main__":
    unittest.main()
