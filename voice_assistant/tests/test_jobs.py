from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bmo_voice.config import Settings
from bmo_voice.events import EventSink
from bmo_voice.jobs import CodexJob, CodexJobManager
from bmo_voice.publish import PreparedPublish, PublishError, PublishResult


class FakeClock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


class RecordingSink:
    def __init__(self):
        self.events: list[dict] = []

    def emit(self, state: str, **fields):
        self.events.append({"state": state, **fields})


class FakePublisher:
    def __init__(self):
        self.prepared: list[str] = []
        self.published: list[PreparedPublish] = []
        self.publish_error = ""

    def prepare(self, project: Path, job_id: str) -> PreparedPublish:
        self.prepared.append(job_id)
        return PreparedPublish(
            project=project,
            job_id=job_id,
            branch=f"bmo/{job_id}",
            base_branch="main",
            base_commit="a" * 40,
        )

    def collect(self, prepared: PreparedPublish) -> PreparedPublish:
        return PreparedPublish(
            project=prepared.project,
            job_id=prepared.job_id,
            branch=prepared.branch,
            base_branch=prepared.base_branch,
            base_commit=prepared.base_commit,
            changed_paths=("app.py",),
            change_digest="digest-app-py",
        )

    def publish(
        self, prepared: PreparedPublish, *, title: str, body: str
    ) -> PublishResult:
        self.published.append(prepared)
        if self.publish_error:
            raise PublishError(self.publish_error)
        return PublishResult(
            commit_sha="b" * 40,
            pr_number=17,
            pr_url="https://github.com/example/demo/pull/17",
        )


class JobTests(unittest.TestCase):
    def _settings(self, root: Path, **changes) -> Settings:
        values = {
            "vosk_model": root,
            "whisper_model": root / "model.bin",
            "codex_bin": "codex",
            "codex_workspace": root / "workspace",
            "codex_project_root": root / "projects",
            "codex_jobs_file": root / "jobs.json",
            "codex_timeout_seconds": 5,
        }
        values.update(changes)
        return Settings(**values)

    def _build_projects(self, root: Path) -> None:
        projects = root / "projects"
        projects.mkdir(parents=True)
        (projects / "demo").mkdir()
        (projects / "other").mkdir()
        (projects / ".hidden").mkdir()

    def test_project_whitelist_only_returns_directories_under_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_projects(root)
            sink = RecordingSink()
            manager = CodexJobManager(self._settings(root), sink, clock=FakeClock())
            projects = [p.name for p in manager._projects()]
            self.assertEqual(sorted(projects), ["demo", "other"])

    def test_resolve_project_rejects_requests_outside_whitelist(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_projects(root)
            sink = RecordingSink()
            manager = CodexJobManager(self._settings(root), sink, clock=FakeClock())
            project, problem = manager.resolve_project("修改 ../etc 项目")
            self.assertIsNone(project)
            self.assertIn("demo", problem)
            self.assertIn("other", problem)

    def test_resolve_project_uses_name_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_projects(root)
            sink = RecordingSink()
            manager = CodexJobManager(self._settings(root), sink, clock=FakeClock())
            project, problem = manager.resolve_project("修登录页", "other")
            self.assertIsNotNone(project)
            self.assertEqual(project.name, "other")
            self.assertEqual(problem, "")

    def test_background_job_runs_to_done_and_notifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_projects(root)
            sink = RecordingSink()
            clock = FakeClock()
            manager = CodexJobManager(
                self._settings(root),
                sink,
                clock=clock,
                publisher=FakePublisher(),
            )

            fake_process = MagicMock()
            fake_process.poll.return_value = 0
            fake_process.returncode = 0
            fake_process.communicate.return_value = (
                json.dumps(
                    {"type": "item.completed", "item": {"type": "agent_message", "text": "改好了"}}
                ),
                "",
            )

            with patch("bmo_voice.jobs.subprocess.Popen", return_value=fake_process):
                job, problem = manager.submit(
                    "修改 demo 项目",
                    request_id="voice-123",
                    confirmed=True,
                )
                self.assertIsNotNone(job)
                self.assertEqual(problem, "")
                manager.wait(job.job_id, timeout=2)

            latest = manager.latest()
            self.assertIsNotNone(latest)
            self.assertEqual(latest.state, "ready")
            self.assertEqual(latest.result, "改好了")
            self.assertEqual(latest.changed_files, ["app.py"])
            messages = manager.drain_notifications()
            self.assertTrue(any("已完成" in m for m in messages))
            self.assertTrue(any(e["state"] == "coding" for e in sink.events))
            prompt = fake_process.communicate.call_args.kwargs["input"]
            self.assertIn("<request_id>voice-123</request_id>", prompt)
            self.assertIn("<confirmed>true</confirmed>", prompt)
            self.assertEqual(
                (root / "jobs.json").stat().st_mode & 0o777,
                0o600,
            )

    def test_cancel_stops_active_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_projects(root)
            sink = RecordingSink()
            clock = FakeClock()
            manager = CodexJobManager(
                self._settings(root),
                sink,
                clock=clock,
                publisher=FakePublisher(),
            )

            started = threading.Event()
            terminated = threading.Event()

            class CancellableProcess:
                returncode = 0

                def poll(self):
                    return None if not terminated.is_set() else 0

                def communicate(self, *_args, **_kwargs):
                    started.set()
                    terminated.wait(timeout=10)
                    return "", ""

                def terminate(self):
                    terminated.set()

                def wait(self, timeout=None):
                    terminated.wait(timeout=timeout)

            fake_process = CancellableProcess()

            with patch("bmo_voice.jobs.subprocess.Popen", return_value=fake_process):
                job, _problem = manager.submit("修改 demo 项目")
                started.wait(timeout=2)
                reply = manager.cancel(job.job_id)
                self.assertIn("正在取消", reply)
                manager.wait(job.job_id, timeout=3)

            latest = manager.latest()
            self.assertIsNotNone(latest)
            self.assertEqual(latest.state, "cancelled")

    def test_cancel_before_process_spawn_still_cancels(self):
        """A cancel that lands while the job is queued must not be lost."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_projects(root)
            sink = RecordingSink()
            manager = CodexJobManager(
                self._settings(root),
                sink,
                clock=FakeClock(),
                publisher=FakePublisher(),
            )

            class QuickProcess:
                pid = None  # killpg(None) fails so the terminate() fallback runs
                returncode = 0

                def __init__(self):
                    self.terminated = False

                def poll(self):
                    return 0 if self.terminated else None

                def terminate(self):
                    self.terminated = True

                def wait(self, timeout=None):
                    return 0

                def communicate(self, *_args, **_kwargs):
                    return "", ""

            spawned: list[QuickProcess] = []

            def factory(*_args, **_kwargs):
                process = QuickProcess()
                spawned.append(process)
                # Cancel lands after the worker thread started but before the
                # process is registered: the old code lost this cancellation.
                manager.cancel()
                return process

            with patch("bmo_voice.jobs.subprocess.Popen", side_effect=factory):
                job, _problem = manager.submit("修改 demo 项目")
                self.assertIsNotNone(job)
                manager.wait(job.job_id, timeout=3)

            latest = manager.latest()
            self.assertIsNotNone(latest)
            self.assertEqual(latest.state, "cancelled")
            self.assertTrue(spawned[0].terminated)
            messages = manager.drain_notifications()
            self.assertTrue(any("已取消" in m for m in messages))

    def test_cancelling_state_is_not_clobbered_by_worker_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_projects(root)
            sink = RecordingSink()
            manager = CodexJobManager(
                self._settings(root), sink, clock=FakeClock()
            )
            with manager._lock:
                job = CodexJob(
                    job_id="job-x",
                    request="r",
                    project=str(root / "projects" / "demo"),
                    state="queued",
                    created_at=1.0,
                    updated_at=1.0,
                )
                manager._jobs.append(job)
            manager.cancel("job-x")
            snapshot = manager._update("job-x", "coding")
            self.assertIsNotNone(snapshot)
            self.assertEqual(snapshot.state, "cancelling")
            self.assertEqual(manager.latest().state, "cancelling")

    def test_restart_marks_active_jobs_interrupted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_projects(root)
            jobs_file = root / "jobs.json"
            jobs_file.write_text(
                json.dumps(
                    {
                        "jobs": [
                            {
                                "job_id": "job-old",
                                "request": "修东西",
                                "project": str(root / "projects" / "demo"),
                                "state": "coding",
                                "created_at": 1.0,
                                "updated_at": 2.0,
                                "result": "",
                                "error": "",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            sink = RecordingSink()
            manager = CodexJobManager(self._settings(root), sink, clock=FakeClock())
            latest = manager.latest()
            self.assertIsNotNone(latest)
            self.assertEqual(latest.state, "interrupted")
            self.assertIn("重启", latest.error)

    def test_retry_last_submits_failed_job_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_projects(root)
            sink = RecordingSink()
            clock = FakeClock()
            manager = CodexJobManager(
                self._settings(root),
                sink,
                clock=clock,
                publisher=FakePublisher(),
            )

            fake_process = MagicMock()
            fake_process.poll.return_value = 0
            fake_process.returncode = 1
            fake_process.communicate.return_value = ("", "编译失败")

            with patch("bmo_voice.jobs.subprocess.Popen", return_value=fake_process):
                job, _ = manager.submit("修改 demo 项目")
                manager.wait(job.job_id, timeout=2)

            self.assertEqual(manager.latest().state, "failed")

            fake_process.reset_mock()
            fake_process.returncode = 0
            fake_process.communicate.return_value = (
                json.dumps(
                    {"type": "item.completed", "item": {"type": "agent_message", "text": "好了"}}
                ),
                "",
            )

            with patch("bmo_voice.jobs.subprocess.Popen", return_value=fake_process):
                new_job, problem = manager.retry_last()
                self.assertIsNotNone(new_job)
                self.assertEqual(problem, "")
                self.assertNotEqual(new_job.job_id, job.job_id)
                manager.wait(new_job.job_id, timeout=2)

            self.assertEqual(manager.latest().state, "ready")

    def test_unconfirmed_high_risk_job_cannot_be_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_projects(root)
            manager = CodexJobManager(
                self._settings(root),
                RecordingSink(),
                clock=FakeClock(),
            )
            with manager._lock:
                manager._jobs.append(
                    CodexJob(
                        job_id="job-risk",
                        request="删除 demo 项目的测试文件",
                        project=str(root / "projects" / "demo"),
                        state="failed",
                        created_at=1.0,
                        updated_at=2.0,
                        confirmed=False,
                    )
                )
            job, problem = manager.retry_last()
            self.assertIsNone(job)
            self.assertIn("高风险", problem)

    def _ready_job(self, root: Path, clock: FakeClock):
        publisher = FakePublisher()
        manager = CodexJobManager(
            self._settings(root),
            RecordingSink(),
            clock=clock,
            publisher=publisher,
        )
        with manager._lock:
            manager._jobs.append(
                CodexJob(
                    job_id="job-ready",
                    request="修改 demo 登录页",
                    project=str(root / "projects" / "demo"),
                    state="ready",
                    created_at=clock(),
                    updated_at=clock(),
                    result="改好了，测试通过",
                    request_id="voice-original",
                    branch="bmo/job-ready",
                    base_branch="main",
                    base_commit="a" * 40,
                    changed_files=["app.py"],
                    change_digest="digest-app-py",
                    publish_offer_expires_at=clock() + 120,
                )
            )
            manager._save_locked()
        return manager, publisher

    def test_publish_does_nothing_before_exact_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_projects(root)
            clock = FakeClock()
            manager, publisher = self._ready_job(root, clock)
            self.assertEqual(publisher.published, [])
            candidate = manager.publish_candidate()
            self.assertEqual(candidate.job_id, "job-ready")
            self.assertEqual(publisher.published, [])

    def test_confirm_publish_binds_request_and_persists_pr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_projects(root)
            clock = FakeClock()
            manager, publisher = self._ready_job(root, clock)
            job, message = manager.confirm_publish(
                "job-ready",
                request_id="voice-confirm-1",
            )
            self.assertIsNotNone(job)
            self.assertIn("正在", message)
            manager.wait("job-ready", timeout=2)
            latest = manager.latest()
            self.assertEqual(latest.state, "published")
            self.assertEqual(
                latest.publish_request_id,
                "voice-confirm-1",
            )
            self.assertEqual(latest.pr_number, 17)
            self.assertEqual(
                latest.pr_url,
                "https://github.com/example/demo/pull/17",
            )
            self.assertEqual(len(publisher.published), 1)

    def test_expired_publish_offer_requires_reoffer_and_new_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_projects(root)
            clock = FakeClock()
            manager, publisher = self._ready_job(root, clock)
            clock.now += 121
            job, message = manager.confirm_publish(
                "job-ready",
                request_id="voice-expired",
            )
            self.assertIsNone(job)
            self.assertIn("超时", message)
            self.assertEqual(publisher.published, [])
            prompt = manager.reoffer_publish("job-ready")
            self.assertIn("确认", prompt)
            job, _message = manager.confirm_publish(
                "job-ready",
                request_id="voice-fresh",
            )
            self.assertIsNotNone(job)
            manager.wait("job-ready", timeout=2)
            self.assertEqual(manager.latest().state, "published")

    def test_decline_and_publish_failure_preserve_local_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_projects(root)
            clock = FakeClock()
            manager, publisher = self._ready_job(root, clock)
            reply = manager.decline_publish("job-ready")
            self.assertIn("保留", reply)
            declined = manager.latest()
            self.assertEqual(declined.state, "declined")
            self.assertEqual(declined.changed_files, ["app.py"])
            self.assertEqual(declined.branch, "bmo/job-ready")

            manager.reoffer_publish("job-ready")
            publisher.publish_error = "auth failed"
            job, _message = manager.confirm_publish(
                "job-ready",
                request_id="voice-confirm-2",
            )
            self.assertIsNotNone(job)
            manager.wait("job-ready", timeout=2)
            failed = manager.latest()
            self.assertEqual(failed.state, "publish_failed")
            self.assertEqual(failed.changed_files, ["app.py"])
            self.assertEqual(failed.branch, "bmo/job-ready")
            self.assertEqual(failed.publish_offer_expires_at, 0)

    def test_restart_does_not_auto_resume_ambiguous_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_projects(root)
            jobs_file = root / "jobs.json"
            jobs_file.write_text(
                json.dumps(
                    {
                        "jobs": [
                            {
                                "job_id": "job-publishing",
                                "request": "修改 demo",
                                "project": str(root / "projects" / "demo"),
                                "state": "publishing",
                                "created_at": 1,
                                "updated_at": 2,
                                "branch": "bmo/job-publishing",
                                "base_branch": "main",
                                "base_commit": "a" * 40,
                                "changed_files": ["app.py"],
                                "change_digest": "digest-app-py",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            publisher = FakePublisher()
            manager = CodexJobManager(
                self._settings(root),
                RecordingSink(),
                clock=FakeClock(),
                publisher=publisher,
            )
            latest = manager.latest()
            self.assertEqual(latest.state, "publish_failed")
            self.assertIn("不会自动", latest.error)
            self.assertEqual(publisher.published, [])


if __name__ == "__main__":
    unittest.main()
