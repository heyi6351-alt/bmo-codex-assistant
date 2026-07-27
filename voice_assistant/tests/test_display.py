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


if __name__ == "__main__":
    unittest.main()
