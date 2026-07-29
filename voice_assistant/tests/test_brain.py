from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bmo_voice.brain import AssistantBrain
from bmo_voice.config import Settings
from bmo_voice.events import EventSink
from bmo_voice.intent import Intent


class FakeCodexBrain:
    def __init__(self):
        self.asks: list[str] = []
        self.requests = []
        self.proactive_prompts: list[tuple[str, bool]] = []

    def ask(self, message: str) -> str:
        self.asks.append(message)
        return "Codex reply"

    def ask_request(self, request) -> str:
        self.requests.append(request)
        return self.ask(request.text)

    def proactive(self, prompt: str, *, allow_writes: bool = False) -> str:
        self.proactive_prompts.append((prompt, allow_writes))
        return "Proactive reply"


class FakeJobManager:
    def __init__(self):
        self.submissions: list[tuple[str, str | None]] = []
        self.cancels: list[str | None] = []
        self.retries: list[()] = []
        self.notifications: list[str] = []
        self.submission_metadata: list[dict] = []
        self.publish_confirms: list[tuple[str, str]] = []
        self.publish_declines: list[str] = []
        self.publish_reoffers: list[str] = []
        self._latest: dict | None = None

    def submit(
        self,
        request: str,
        project_hint: str | None = None,
        **metadata,
    ):
        self.submissions.append((request, project_hint))
        self.submission_metadata.append(metadata)
        self._latest = {
            "job_id": "job-1",
            "request": request,
            "project": "/tmp/projects/demo",
            "state": "queued",
            "created_at": 0,
            "updated_at": 0,
            **metadata,
        }
        from bmo_voice.jobs import CodexJob

        return CodexJob.from_dict(self._latest), ""

    def latest(self):
        from bmo_voice.jobs import CodexJob

        return CodexJob.from_dict(self._latest) if self._latest else None

    def status_text(self) -> str:
        return "job-1 正在运行。"

    def cancel(self, job_id: str | None = None) -> str:
        self.cancels.append(job_id)
        return "正在取消 job-1。"

    def retry_last(self):
        self.retries.append(())
        latest = self.latest()
        if latest is None:
            return None, "没有可以重新执行的任务。"
        return self.submit(latest.request, Path(latest.project).name)

    def drain_notifications(self) -> list[str]:
        out = self.notifications[:]
        self.notifications.clear()
        return out

    def publish_candidate(self):
        latest = self.latest()
        if latest and latest.state in {
            "ready",
            "publish_failed",
            "declined",
        }:
            return latest
        return None

    def confirm_publish(self, job_id: str, *, request_id: str):
        self.publish_confirms.append((job_id, request_id))
        latest = self.latest()
        if latest is not None:
            latest.state = "publishing"
        return latest, f"正在为 {job_id} 创建 GitHub PR。"

    def decline_publish(self, job_id: str) -> str:
        self.publish_declines.append(job_id)
        return "好的，不发布。改动保留在本地分支。"

    def reoffer_publish(self, job_id: str | None = None) -> str:
        self.publish_reoffers.append(job_id or "")
        return "请说确认发布或不发布。"


class FakeScheduler:
    def __init__(self):
        self.suppress_today_calls = 0
        self.resume_calls = 0
        self.snooze_minutes: list[int] = []

    def suppress_today(self) -> None:
        self.suppress_today_calls += 1

    def resume_reminders(self) -> None:
        self.resume_calls += 1

    def snooze(self, minutes: int) -> None:
        self.snooze_minutes.append(minutes)


class BrainTests(unittest.TestCase):
    def _settings(self, root: Path) -> Settings:
        return Settings(
            vosk_model=root,
            whisper_model=root / "model.bin",
        )

    def _brain(self, clock=None):
        self.sink = RecordingSink()
        self.codex = FakeCodexBrain()
        self.jobs = FakeJobManager()
        self.scheduler = FakeScheduler()
        with tempfile.TemporaryDirectory() as tmp:
            self.settings = self._settings(Path(tmp))
            kwargs = {"clock": clock} if clock is not None else {}
            return AssistantBrain(
                self.settings,
                self.sink,
                self.codex,
                self.jobs,
                self.scheduler,
                **kwargs,
            )

    def test_question_does_not_call_tools_or_jobs(self):
        brain = self._brain()
        reply = brain.ask("量子纠缠是什么")
        self.assertEqual(reply, "Codex reply")
        self.assertEqual(len(self.codex.asks), 1)
        self.assertEqual(self.codex.asks[0], "量子纠缠是什么")
        self.assertEqual(len(self.jobs.submissions), 0)
        states = [e["state"] for e in self.sink.events]
        self.assertNotIn("tool_call", states)
        self.assertNotIn("coding", states)

    def _brain_with_question_brain(self, question):
        self.sink = RecordingSink()
        self.codex = FakeCodexBrain()
        self.jobs = FakeJobManager()
        self.scheduler = FakeScheduler()
        with tempfile.TemporaryDirectory() as tmp:
            return AssistantBrain(
                self._settings(Path(tmp)),
                self.sink,
                self.codex,
                self.jobs,
                self.scheduler,
                question_brain=question,
            )

    def test_question_uses_question_brain_when_provided(self):
        question = FakeCodexBrain()
        brain = self._brain_with_question_brain(question)
        reply = brain.ask("量子纠缠是什么")
        self.assertEqual(reply, "Codex reply")
        # Question offloaded to the cheap text brain, not Codex.
        self.assertEqual(len(question.asks), 1)
        self.assertEqual(len(self.codex.asks), 0)

    def test_action_still_uses_codex_with_separate_question_brain(self):
        question = FakeCodexBrain()
        brain = self._brain_with_question_brain(question)
        reply = brain.ask("查看我今天的日历")
        self.assertEqual(reply, "Codex reply")
        # Actions need Codex tool execution; the text brain must not see them.
        self.assertEqual(len(self.codex.asks), 1)
        self.assertEqual(len(question.asks), 0)
        self.assertTrue(
            any(e["state"] == "tool_call" for e in self.sink.events)
        )

    def test_action_with_high_risk_waits_for_confirmation(self):
        brain = self._brain()
        reply = brain.ask("发送邮件给张三")
        self.assertIn("确认", reply)
        self.assertTrue(brain.awaiting_confirmation)
        self.assertEqual(len(self.codex.asks), 0)

    def test_confirmation_yes_executes_action(self):
        brain = self._brain()
        brain.ask("发送邮件给张三")
        reply = brain.ask("确认")
        self.assertEqual(reply, "Codex reply")
        self.assertFalse(brain.awaiting_confirmation)
        self.assertEqual(len(self.codex.asks), 1)
        self.assertEqual(self.codex.asks[0], "发送邮件给张三")
        self.assertTrue(self.codex.requests[0].confirmed)
        self.assertTrue(any(e["state"] == "tool_call" for e in self.sink.events))

    def test_confirmation_no_cancels(self):
        brain = self._brain()
        brain.ask("发送邮件给张三")
        reply = brain.ask("取消")
        self.assertIn("已取消", reply)
        self.assertFalse(brain.awaiting_confirmation)
        self.assertEqual(len(self.codex.asks), 0)

    def test_mixed_yes_and_no_is_treated_as_no(self):
        brain = self._brain()
        brain.ask("发送邮件给张三")
        reply = brain.ask("好的，那算了")
        self.assertIn("已取消", reply)
        self.assertEqual(len(self.codex.asks), 0)

    def test_negated_yes_is_treated_as_no(self):
        brain = self._brain()
        brain.ask("发送邮件给张三")
        reply = brain.ask("不确定")
        self.assertIn("已取消", reply)
        self.assertEqual(len(self.codex.asks), 0)

    def test_sentence_containing_short_yes_word_does_not_confirm(self):
        brain = self._brain()
        brain.ask("发送邮件给张三")
        reply = brain.ask("这个方案好像有问题")
        self.assertIn("确认", reply)
        self.assertTrue(brain.awaiting_confirmation)
        self.assertEqual(len(self.codex.asks), 0)

    def test_confirmation_timeout_expires_pending_action(self):
        now = [1000.0]

        def fake_clock():
            return now[0]

        brain = self._brain(clock=fake_clock)
        brain.ask("发送邮件给张三")
        self.assertTrue(brain.awaiting_confirmation)
        # Default confirmation_timeout_seconds is 120.
        now[0] += 121
        reply = brain.ask("确认")
        self.assertIn("超时", reply)
        self.assertFalse(brain.awaiting_confirmation)
        self.assertEqual(len(self.codex.asks), 0)

    def test_fresh_utterance_after_timeout_is_processed_normally(self):
        now = [1000.0]

        def fake_clock():
            return now[0]

        brain = self._brain(clock=fake_clock)
        brain.ask("发送邮件给张三")
        now[0] += 121
        reply = brain.ask("量子纠缠是什么")
        self.assertEqual(reply, "Codex reply")
        self.assertEqual(self.codex.asks, ["量子纠缠是什么"])

    def test_high_risk_coding_requires_confirmation(self):
        brain = self._brain()
        reply = brain.ask("删除 demo 项目的测试文件")
        self.assertIn("确认", reply)
        self.assertTrue(brain.awaiting_confirmation)
        self.assertEqual(len(self.jobs.submissions), 0)
        reply = brain.ask("确认")
        self.assertIn("已提交后台任务", reply)
        self.assertEqual(len(self.jobs.submissions), 1)
        self.assertTrue(self.jobs.submission_metadata[0]["confirmed"])

    def test_task_creation_is_not_hijacked_as_progress_query(self):
        brain = self._brain()
        reply = brain.ask("帮我创建一个任务")
        self.assertEqual(reply, "Codex reply")
        self.assertEqual(len(self.codex.asks), 1)

    def test_coding_submits_background_job(self):
        brain = self._brain()
        reply = brain.ask("修改 demo 项目的登录页")
        self.assertIn("已提交后台任务", reply)
        self.assertEqual(len(self.jobs.submissions), 1)
        self.assertTrue(any(e["state"] == "coding" for e in self.sink.events))

    def test_progress_query_returns_job_status(self):
        brain = self._brain()
        reply = brain.ask("进度怎么样了")
        self.assertEqual(reply, "job-1 正在运行。")

    def test_cancel_calls_job_manager(self):
        brain = self._brain()
        reply = brain.ask("取消当前任务")
        self.assertIn("正在取消", reply)
        self.assertEqual(self.jobs.cancels, [None])

    def test_retry_calls_job_manager(self):
        brain = self._brain()
        self.jobs._latest = {
            "job_id": "job-old",
            "request": "修 old",
            "project": "/tmp/projects/old",
            "state": "failed",
            "created_at": 0,
            "updated_at": 0,
        }
        reply = brain.ask("重试")
        self.assertIn("已重新提交", reply)
        self.assertEqual(len(self.jobs.retries), 1)

    def test_suppress_today_preference(self):
        brain = self._brain()
        reply = brain.ask("今天别提醒我")
        self.assertIn("今天不再", reply)
        self.assertEqual(self.scheduler.suppress_today_calls, 1)
        self.assertTrue(any(e["state"] == "reminder" for e in self.sink.events))

    def test_resume_reminders_preference(self):
        brain = self._brain()
        reply = brain.ask("恢复提醒")
        self.assertIn("已恢复", reply)
        self.assertEqual(self.scheduler.resume_calls, 1)

    def test_snooze_preference(self):
        brain = self._brain()
        reply = brain.ask("推迟 10 分钟")
        self.assertIn("10 分钟", reply)
        self.assertEqual(self.scheduler.snooze_minutes, [10])

    def test_context_continuation_uses_latest_project_hint(self):
        brain = self._brain()
        self.jobs._latest = {
            "job_id": "job-old",
            "request": "修 old",
            "project": "/tmp/projects/old",
            "state": "failed",
            "created_at": 0,
            "updated_at": 0,
        }
        brain.ask("重试")
        self.assertEqual(self.jobs.submissions[-1][1], "old")

    def test_ready_job_requires_explicit_publish_confirmation(self):
        brain = self._brain()
        self.jobs._latest = {
            "job_id": "job-ready",
            "request": "修改 demo",
            "project": "/tmp/projects/demo",
            "state": "ready",
            "created_at": 0,
            "updated_at": 0,
            "changed_files": ["app.py"],
        }
        reply = brain.ask("确认发布")
        self.assertIn("正在", reply)
        self.assertEqual(len(self.jobs.publish_confirms), 1)
        job_id, request_id = self.jobs.publish_confirms[0]
        self.assertEqual(job_id, "job-ready")
        self.assertTrue(request_id.startswith("voice-"))

    def test_publish_negative_wins_and_never_confirms(self):
        brain = self._brain()
        self.jobs._latest = {
            "job_id": "job-ready",
            "request": "修改 demo",
            "project": "/tmp/projects/demo",
            "state": "ready",
            "created_at": 0,
            "updated_at": 0,
            "changed_files": ["app.py"],
        }
        reply = brain.ask("好的，那先不发布")
        self.assertIn("不发布", reply)
        self.assertEqual(self.jobs.publish_declines, ["job-ready"])
        self.assertEqual(self.jobs.publish_confirms, [])

    def test_publish_failure_retry_rearms_instead_of_publishing(self):
        brain = self._brain()
        self.jobs._latest = {
            "job_id": "job-failed",
            "request": "修改 demo",
            "project": "/tmp/projects/demo",
            "state": "publish_failed",
            "created_at": 0,
            "updated_at": 0,
            "changed_files": ["app.py"],
        }
        reply = brain.ask("重新发布")
        self.assertIn("确认", reply)
        self.assertEqual(self.jobs.publish_reoffers, ["job-failed"])
        self.assertEqual(self.jobs.publish_confirms, [])


class RecordingSink:
    def __init__(self):
        self.events: list[dict] = []

    def emit(self, state: str, **fields):
        self.events.append({"state": state, **fields})


if __name__ == "__main__":
    unittest.main()
