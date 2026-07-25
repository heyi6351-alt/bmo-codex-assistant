from __future__ import annotations

import asyncio
import os
import platform
import tempfile
import unittest
from pathlib import Path

from bmo_worker.capabilities import CapabilityError, CapabilityRegistry
from bmo_worker.codex_runner import CodexRunner
from bmo_worker.config import Settings
from bmo_worker.store import JobStore


class CapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.settings = Settings(
            token="test-pairing-token-with-24-chars",
            workspace_root=root / "projects",
            database_path=root / "worker.sqlite3",
            artifacts_root=root / "artifacts",
            codex_bin="codex-does-not-exist",
        )
        self.settings.prepare()
        self.store = JobStore(self.settings.database_path)
        self.store.initialize()
        self.runner = CodexRunner(self.settings, self.store)
        self.registry = CapabilityRegistry(self.settings, self.store, self.runner)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_file_read_is_bounded_and_secret_names_are_denied(self) -> None:
        project = self.settings.workspace_root / "demo"
        project.mkdir()
        (project / "notes.txt").write_text("BMO remembers this", encoding="utf-8")
        (project / ".env").write_text("SECRET=nope", encoding="utf-8")

        prepared = self.registry.prepare(
            "files.read",
            {"root": "projects", "path": "demo/notes.txt"},
            True,
        )
        output = self.registry._execute_read_capability(
            prepared.action, prepared.arguments
        )
        self.assertEqual(output["content"], "BMO remembers this")
        with self.assertRaises(CapabilityError):
            self.registry.prepare(
                "files.read",
                {"root": "projects", "path": "demo/.env"},
                True,
            )

    @unittest.skipIf(os.name == "nt", "Windows symlink creation needs privileges")
    def test_file_read_rejects_symlink(self) -> None:
        project = self.settings.workspace_root / "demo"
        project.mkdir()
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        (project / "linked.txt").symlink_to(outside)
        with self.assertRaises(CapabilityError):
            self.registry.prepare(
                "files.read",
                {"root": "projects", "path": "demo/linked.txt"},
                True,
            )

    def test_system_snapshot_omits_host_identity_and_paths(self) -> None:
        prepared = self.registry.prepare("system.snapshot", {}, False)
        output = self.registry._execute_read_capability(
            prepared.action, prepared.arguments
        )
        self.assertNotIn("hostname", output)
        self.assertNotIn("workspace", output)
        self.assertIn("worker_version", output)

    def test_codex_command_never_uses_unrestricted_mode(self) -> None:
        command = self.runner._build_command(
            self.settings.workspace_root / "demo", None
        )
        rendered = " ".join(command)
        self.assertIn("workspace-write", rendered)
        self.assertNotIn("yolo", rendered)
        self.assertNotIn("dangerously", rendered)
        self.assertNotIn("skip-git-repo-check", rendered)

    def test_inline_capability_completes_with_structured_output(self) -> None:
        prepared = self.registry.prepare("system.snapshot", {}, False)
        job, _ = self.store.create_job(
            action=prepared.action,
            project_id=prepared.project_id,
            goal=prepared.goal,
            workspace=prepared.workspace,
            client_request_id="snapshot-request",
            arguments=prepared.arguments,
        )
        claimed = self.store.claim_next_job()
        assert claimed is not None
        asyncio.run(self.registry.execute(claimed))
        current = self.store.get_job(job["id"])
        assert current is not None
        self.assertEqual(current["state"], "done")
        self.assertEqual(current["output"]["os"], platform.system())


if __name__ == "__main__":
    unittest.main()
