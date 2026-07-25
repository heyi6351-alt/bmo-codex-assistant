from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from bmo_worker.config import Settings
from bmo_worker.service import create_app


class WorkerApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.token = "test-pairing-token-with-24-chars"
        settings = Settings(
            token=self.token,
            workspace_root=root / "projects",
            database_path=root / "worker.sqlite3",
            artifacts_root=root / "artifacts",
            codex_bin="codex-does-not-exist-for-api-tests",
            max_runtime_seconds=5,
        )
        self.client_context = TestClient(create_app(settings))
        self.client = self.client_context.__enter__()
        self.auth = {"Authorization": f"Bearer {self.token}"}

    def tearDown(self) -> None:
        self.client_context.__exit__(None, None, None)
        self.temp.cleanup()

    def test_health_is_minimal_and_does_not_require_auth(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()["codex_available"])

    def test_job_api_requires_pairing_token(self) -> None:
        response = self.client.post(
            "/v1/jobs",
            json={
                "project_id": "demo-site",
                "goal": "Build a small demonstration website",
            },
        )
        self.assertEqual(response.status_code, 401, response.text)

    def test_project_path_is_not_accepted(self) -> None:
        response = self.client.post(
            "/v1/jobs",
            headers=self.auth,
            json={
                "capability": "files.read",
                "arguments": {"root": "projects", "path": "../escape"},
                "confirmed": True,
            },
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_authenticated_job_is_accepted(self) -> None:
        response = self.client.post(
            "/v1/jobs",
            headers=self.auth,
            json={
                "capability": "system.snapshot",
                "arguments": {},
                "client_request_id": "voice-request-api-001",
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        payload = response.json()
        self.assertTrue(payload["created"])
        self.assertEqual(payload["job"]["action"], "system.snapshot")
        self.assertNotIn("workspace", payload["job"])

    def test_capability_discovery_is_authenticated_and_typed(self) -> None:
        self.assertEqual(self.client.get("/v1/capabilities").status_code, 401)
        response = self.client.get("/v1/capabilities", headers=self.auth)
        self.assertEqual(response.status_code, 200, response.text)
        capabilities = {
            item["id"]: item for item in response.json()["capabilities"]
        }
        self.assertEqual(capabilities["files.read"]["execution"], "inline")
        self.assertEqual(
            capabilities["developer.website.build"]["state"], "setup_required"
        )
        self.assertEqual(capabilities["email.read"]["state"], "setup_required")

    def test_sensitive_read_requires_confirmation(self) -> None:
        response = self.client.post(
            "/v1/jobs",
            headers=self.auth,
            json={
                "capability": "files.list",
                "arguments": {"root": "projects", "path": "."},
            },
        )
        self.assertEqual(response.status_code, 428, response.text)

    def test_unconfigured_email_fails_closed(self) -> None:
        response = self.client.post(
            "/v1/jobs",
            headers=self.auth,
            json={
                "capability": "email.read",
                "arguments": {"message_id": "123"},
                "confirmed": True,
            },
        )
        self.assertEqual(response.status_code, 503, response.text)


if __name__ == "__main__":
    unittest.main()
