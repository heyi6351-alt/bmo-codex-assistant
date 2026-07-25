from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from bmo_worker.verifier import verify_static_site


class BrowserVerifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_self_contained_site_passes_and_captures_two_sizes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "site"
            workspace.mkdir()
            (workspace / "index.html").write_text(
                """
                <!doctype html>
                <html>
                  <head><title>BMO Test Site</title></head>
                  <body>
                    <main>
                      <h1>Hello from BMO</h1>
                      <p>This self-contained website is ready for verification.</p>
                    </main>
                  </body>
                </html>
                """,
                encoding="utf-8",
            )
            report = await verify_static_site(workspace, root / "artifacts")
            self.assertTrue(report["passed"], report)
            self.assertEqual(
                set(report["screenshots"]), {"desktop.png", "mobile.png"}
            )
            for screenshot in report["screenshots"]:
                self.assertTrue((root / "artifacts" / screenshot).is_file())

    async def test_other_localhost_ports_are_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "site"
            workspace.mkdir()
            (workspace / "index.html").write_text(
                """
                <!doctype html>
                <html>
                  <body>
                    <h1>BMO network boundary test</h1>
                    <p>This page has enough visible content for verification.</p>
                    <script>fetch("http://127.0.0.1:9/private")</script>
                  </body>
                </html>
                """,
                encoding="utf-8",
            )
            report = await verify_static_site(workspace, root / "artifacts")
            self.assertFalse(report["passed"])
            self.assertTrue(
                any("127.0.0.1:9" in url for url in report["blocked_requests"])
            )

    @unittest.skipIf(os.name == "nt", "symlink needs privileges")
    async def test_workspace_symlink_is_rejected_before_serving(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "site"
            workspace.mkdir()
            (workspace / "index.html").write_text(
                "<html><body>BMO rejects linked host files.</body></html>",
                encoding="utf-8",
            )
            outside = root / "outside.txt"
            outside.write_text("host secret", encoding="utf-8")
            (workspace / "linked.txt").symlink_to(outside)
            report = await verify_static_site(workspace, root / "artifacts")
            self.assertFalse(report["passed"])
            self.assertIn("symbolic links", report["issues"][0])


if __name__ == "__main__":
    unittest.main()
