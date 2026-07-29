from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

from bmo_voice.display import build_server, load_state


class DisplayTests(unittest.TestCase):
    def test_missing_or_invalid_state_is_idle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            self.assertEqual(load_state(path)["state"], "idle")
            path.write_text("[]", encoding="utf-8")
            self.assertEqual(load_state(path)["state"], "idle")
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(load_state(path)["state"], "idle")

    def test_state_and_static_face_are_served(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            web = root / "web"
            web.mkdir()
            (web / "index.html").write_text("<title>BMO</title>", encoding="utf-8")
            state = root / "state.json"
            state.write_text(
                json.dumps({"state": "thinking", "reply": "稍等"}),
                encoding="utf-8",
            )
            server = build_server("127.0.0.1", 0, web, state)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urllib.request.urlopen(f"{base}/api/state") as response:
                    payload = json.load(response)
                    self.assertEqual(payload["state"], "thinking")
                    self.assertEqual(response.headers["Cache-Control"], "no-store")
                with urllib.request.urlopen(f"{base}/") as response:
                    self.assertIn("BMO", response.read().decode("utf-8"))
                with urllib.request.urlopen(f"{base}/healthz") as response:
                    self.assertTrue(json.load(response)["ok"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_state_api_returns_extended_voice_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            web = root / "web"
            web.mkdir()
            (web / "index.html").write_text("<title>BMO</title>", encoding="utf-8")
            state = root / "state.json"
            state.write_text(
                json.dumps(
                    {
                        "state": "coding",
                        "intent": "coding",
                        "transcript": "改登录页",
                        "subtitle": "demo：任务已提交",
                        "job": {
                            "id": "job-1",
                            "project": "demo",
                            "state": "queued",
                            "pr_url": (
                                "https://github.com/example/demo/pull/17"
                            ),
                        },
                    }
                ),
                encoding="utf-8",
            )
            server = build_server("127.0.0.1", 0, web, state)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                with urllib.request.urlopen(f"{base}/api/state") as response:
                    payload = json.load(response)
                    self.assertEqual(payload["state"], "coding")
                    self.assertEqual(payload["intent"], "coding")
                    self.assertEqual(payload["job"]["project"], "demo")
                    self.assertEqual(
                        payload["job"]["pr_url"],
                        "https://github.com/example/demo/pull/17",
                    )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_face_html_contains_new_state_elements(self) -> None:
        web = Path(__file__).resolve().parents[1] / "web"
        body = (web / "index.html").read_text(encoding="utf-8")
        script = (web / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="intent"', body)
        self.assertIn('id="connection"', body)
        self.assertIn('id="subtitle"', body)
        self.assertIn('id="job-card"', body)
        self.assertIn('id="job-state"', body)
        self.assertIn('id="pr-row"', body)
        self.assertIn('id="pr-url"', body)
        self.assertIn('"published"', script)
        self.assertIn("job.pr_url", script)


if __name__ == "__main__":
    unittest.main()
