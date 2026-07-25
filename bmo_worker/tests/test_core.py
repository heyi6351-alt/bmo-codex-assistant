from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bmo_worker.config import safe_project_path, validate_project_id
from bmo_worker.store import JobStore


class WorkspaceBoundaryTests(unittest.TestCase):
    def test_valid_project_id_stays_below_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(
                safe_project_path(root, "my-site"), (root / "my-site").resolve()
            )

    def test_paths_and_invalid_names_are_rejected(self) -> None:
        for value in ("../escape", "two/levels", "x", "bad space"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_project_id(value)

    def test_project_id_is_normalized(self) -> None:
        self.assertEqual(validate_project_id("My-Site"), "my-site")


class JobStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = JobStore(self.root / "worker.sqlite3")
        self.store.initialize()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_idempotent_job_creation_and_event_cursor(self) -> None:
        first, created = self.store.create_job(
            action="build_website",
            project_id="demo-site",
            goal="Build a demo",
            workspace=self.root / "demo-site",
            client_request_id="voice-request-001",
        )
        second, created_again = self.store.create_job(
            action="build_website",
            project_id="demo-site",
            goal="Build a demo",
            workspace=self.root / "demo-site",
            client_request_id="voice-request-001",
        )
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first["id"], second["id"])

        self.store.add_event(first["id"], "planning", "BMO is planning")
        all_events = self.store.events_after(first["id"], 0)
        later_events = self.store.events_after(first["id"], 1)
        self.assertEqual([event["seq"] for event in all_events], [1, 2])
        self.assertEqual([event["seq"] for event in later_events], [2])

    def test_cancelled_queued_job_is_never_claimed(self) -> None:
        job, _ = self.store.create_job(
            action="build_website",
            project_id="cancel-me",
            goal="Build nothing",
            workspace=self.root / "cancel-me",
            client_request_id=None,
        )
        self.assertTrue(self.store.request_cancel(job["id"]))
        current = self.store.get_job(job["id"])
        assert current is not None
        self.assertEqual(current["state"], "cancelled")
        self.assertIsNone(self.store.claim_next_job())

    def test_idempotency_key_cannot_change_meaning(self) -> None:
        self.store.create_job(
            action="system.snapshot",
            project_id="",
            goal="Inspect this PC",
            workspace=Path(),
            client_request_id="same-request-id",
            arguments={},
        )
        with self.assertRaises(ValueError):
            self.store.create_job(
                action="files.list",
                project_id="",
                goal="List files",
                workspace=Path(),
                client_request_id="same-request-id",
                arguments={"root": "projects", "path": "."},
            )

    def test_restart_fails_every_active_phase(self) -> None:
        jobs = []
        for state in ("running", "coding", "verifying", "repairing", "working"):
            job, _ = self.store.create_job(
                action="system.snapshot",
                project_id="",
                goal="Inspect",
                workspace=Path(),
                client_request_id=f"restart-{state}",
                arguments={},
            )
            self.store.update_job(job["id"], state=state)
            jobs.append(job["id"])
        self.assertEqual(self.store.recover_interrupted_jobs(), len(jobs))
        for job_id in jobs:
            current = self.store.get_job(job_id)
            assert current is not None
            self.assertEqual(current["state"], "failed")


if __name__ == "__main__":
    unittest.main()
