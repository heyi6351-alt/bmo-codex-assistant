from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from bmo_voice.audio import Microphone, contains_phrase, normalize_speech
from bmo_voice.codex import CodexBrain, parse_codex_events
from bmo_voice.config import ConfigurationError, Settings
from bmo_voice.core import ConversationController
from bmo_voice.events import EventSink
from bmo_voice.intent import Intent, codex_envelope, route_request
from bmo_voice.lark import LarkClient, Meeting, parse_agenda
from bmo_voice.proactive import ProactiveScheduler


class FakeMicrophone:
    def __init__(self):
        self.clears = 0

    def clear(self) -> None:
        self.clears += 1


class FakeWake:
    def wait_for_wake(self, _microphone, _timeout) -> bool:
        return True


class FakeTranscriber:
    def __init__(self, transcripts):
        self.transcripts = iter(transcripts)
        self.followups = []

    def transcribe(self, _microphone, *, followup=False) -> str:
        self.followups.append(followup)
        return next(self.transcripts, "")


class FakeBrain:
    def __init__(self):
        self.messages = []
        self.proactive_prompts = []

    def ask(self, message: str) -> str:
        self.messages.append(message)
        return f"回复：{message}"

    def proactive(self, prompt: str, *, allow_writes=False) -> str:
        self.proactive_prompts.append((prompt, allow_writes))
        return "这是你的主动简报"


class FakeSpeaker:
    def __init__(self):
        self.spoken = []

    def speak(self, text: str) -> None:
        self.spoken.append(text)


class FakeInterruptSpeaker(FakeSpeaker):
    def __init__(self):
        super().__init__()
        self.interruptible = []

    def speak_interruptible(
        self, text, _microphone, *, energy_threshold, chunks_required
    ):
        self.spoken.append(text)
        self.interruptible.append(
            (text, energy_threshold, chunks_required)
        )
        return True


class FakeLark:
    def __init__(self, meetings=None):
        self.meetings = meetings or []
        self.calls = 0

    def agenda(self, _start, _end):
        self.calls += 1
        return self.meetings

    def incomplete_tasks(self, _due_end=None):
        return {"items": [{"summary": "完成前端", "completed": False}]}

    def messages(self, _query, _start, _end):
        return {"items": []}


class VoiceCoreTests(unittest.TestCase):
    def test_wake_phrase_matching_is_bilingual(self) -> None:
        self.assertEqual(normalize_speech(" 你好 BMO。"), "你好bmo")
        self.assertTrue(contains_phrase("你好 b m o", ("你好BMO",)))
        self.assertTrue(contains_phrase("hey b m o", ("Hey BMO",)))
        self.assertTrue(contains_phrase("哔某", ("BMO", "哔某", "比莫")))
        self.assertTrue(contains_phrase("比莫", ("BMO", "哔某", "比莫")))

    def test_microphone_replays_barge_in_audio_in_original_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            microphone = Microphone(
                Settings(vosk_model=root, whisper_model=root / "model.bin")
            )
            microphone.replay([b"first", b"second"])
            self.assertEqual(microphone.read(), b"first")
            self.assertEqual(microphone.read(), b"second")

    def test_multi_turn_session_keeps_one_brain_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcriber = FakeTranscriber(["今天天气怎么样", "帮我记住带伞", ""])
            brain = FakeBrain()
            speaker = FakeSpeaker()
            controller = ConversationController(
                FakeWake(),
                transcriber,
                brain,
                speaker,
                EventSink(Path(tmp) / "state.json"),
                max_turns=5,
            )
            microphone = FakeMicrophone()
            completed = controller.run_session(microphone)
            self.assertEqual(completed, 2)
            self.assertEqual(brain.messages, ["今天天气怎么样", "帮我记住带伞"])
            self.assertEqual(speaker.spoken[0], "我在")
            self.assertEqual(transcriber.followups, [False, True, True])
            self.assertGreaterEqual(microphone.clears, 3)
            state = json.loads((Path(tmp) / "state.json").read_text())
            self.assertEqual(state["state"], "idle")

    def test_reply_can_be_interrupted_with_configured_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            speaker = FakeInterruptSpeaker()
            controller = ConversationController(
                FakeWake(),
                FakeTranscriber(["先回答这个", ""]),
                FakeBrain(),
                speaker,
                EventSink(Path(tmp) / "state.json"),
                max_turns=2,
                barge_in_enabled=True,
                barge_in_energy_threshold=700,
                barge_in_chunks=3,
            )
            self.assertEqual(controller.run_session(FakeMicrophone()), 1)
            self.assertEqual(
                speaker.interruptible,
                [("回复：先回答这个", 700, 3)],
            )

    def test_stop_phrase_does_not_call_brain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            brain = FakeBrain()
            speaker = FakeSpeaker()
            controller = ConversationController(
                FakeWake(),
                FakeTranscriber(["不用了"]),
                brain,
                speaker,
                EventSink(Path(tmp) / "state.json"),
                max_turns=5,
            )
            self.assertEqual(controller.run_session(FakeMicrophone()), 0)
            self.assertEqual(brain.messages, [])
            self.assertIn("好的，需要我时再叫我", speaker.spoken)


class IntentTests(unittest.TestCase):
    def test_default_is_question(self) -> None:
        self.assertEqual(route_request("日历是怎么工作的").intent, Intent.QUESTION)
        self.assertEqual(route_request("Explain CSS subgrid").intent, Intent.QUESTION)
        self.assertEqual(
            route_request("如何删除 Git 项目的测试分支").intent,
            Intent.QUESTION,
        )
        self.assertEqual(
            route_request("How do I delete a repository branch?").intent,
            Intent.QUESTION,
        )

    def test_explicit_execution_routes(self) -> None:
        self.assertEqual(
            route_request("修改 demo 项目的登录页").intent, Intent.CODING
        )
        self.assertEqual(
            route_request("删除 demo 项目的旧测试").intent,
            Intent.CODING,
        )
        self.assertEqual(route_request("查看我今天的日程").intent, Intent.ACTION)
        self.assertEqual(route_request("我今天有什么日程").intent, Intent.ACTION)
        self.assertEqual(
            route_request("明天下午三点创建产品会").intent, Intent.ACTION
        )
        self.assertEqual(route_request("那你直接改吧").intent, Intent.CODING)

    def test_question_envelope_forbids_tools(self) -> None:
        envelope = codex_envelope(route_request("为什么天空是蓝色"))
        self.assertIn("<voice_intent>question</voice_intent>", envelope)
        self.assertIn("do not edit files", envelope)


class CodexTests(unittest.TestCase):
    def test_jsonl_reply_and_thread_are_extracted(self) -> None:
        payload = "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "今天没有任务"},
                    }
                ),
            ]
        )
        self.assertEqual(parse_codex_events(payload), ("thread-1", "今天没有任务"))

    @patch("bmo_voice.codex.subprocess.run")
    def test_codex_persists_thread_and_uses_read_only_for_question(self, run) -> None:
        run.return_value.returncode = 0
        run.return_value.stdout = "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "回答"},
                    }
                ),
            ]
        )
        run.return_value.stderr = ""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                vosk_model=root,
                whisper_model=root / "model.bin",
                codex_workspace=root / "brain",
                codex_project_root=root / "projects",
                codex_session_file=root / "session.json",
            )
            self.assertEqual(CodexBrain(settings).ask("天空为什么是蓝色"), "回答")
            command = run.call_args.args[0]
            self.assertIn("read-only", command)
            self.assertNotIn("--ignore-user-config", command)
            self.assertIn("<voice_intent>question</voice_intent>", run.call_args.kwargs["input"])
            saved = json.loads((root / "session.json").read_text())
            self.assertEqual(saved["thread_id"], "thread-1")
            self.assertEqual(
                (root / "session.json").stat().st_mode & 0o777,
                0o600,
            )

    @patch("bmo_voice.codex.subprocess.run")
    def test_exact_confirmation_metadata_reaches_codex(self, run) -> None:
        run.return_value.returncode = 0
        run.return_value.stdout = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": "已发送"},
            }
        )
        run.return_value.stderr = ""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                vosk_model=root,
                whisper_model=root / "model.bin",
                codex_workspace=root / "brain",
                codex_project_root=root / "projects",
                codex_session_file=root / "session.json",
            )
            request = replace(
                route_request("发送邮件给张三"),
                confirmed=True,
            )
            self.assertEqual(
                CodexBrain(settings).ask_request(request),
                "已发送",
            )
            prompt = run.call_args.kwargs["input"]
            self.assertIn("<confirmed>true</confirmed>", prompt)
            self.assertIn(f"<request_id>{request.request_id}</request_id>", prompt)


class LarkTests(unittest.TestCase):
    def test_agenda_timestamp_dict_is_parsed(self) -> None:
        payload = {
            "data": {
                "events": [
                    {
                        "event_id": "evt-1",
                        "summary": "产品会",
                        "start_time": {
                            "timestamp": "1785128400",
                            "timezone": "Asia/Shanghai",
                        },
                    }
                ]
            }
        }
        meetings = parse_agenda(payload, "Asia/Shanghai")
        self.assertEqual(meetings[0].event_id, "evt-1")
        self.assertEqual(meetings[0].summary, "产品会")
        self.assertIsNotNone(meetings[0].start.tzinfo)

    @patch("bmo_voice.lark.subprocess.run")
    def test_read_commands_use_user_identity_without_a_shell(self, run) -> None:
        run.return_value.returncode = 0
        run.return_value.stdout = '{"events":[]}'
        run.return_value.stderr = ""
        settings = Settings(vosk_model=Path("/unused"))
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        self.assertEqual(LarkClient(settings).agenda(now, now + timedelta(days=1)), [])
        command = run.call_args.args[0]
        self.assertEqual(command[0], "lark-cli")
        self.assertIn("--as", command)
        self.assertEqual(command[command.index("--as") + 1], "user")
        self.assertFalse(run.call_args.kwargs.get("shell", False))


class EventTests(unittest.TestCase):
    def test_transient_fields_clear_but_active_job_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            sink = EventSink(path)
            sink.emit(
                "coding",
                intent="coding",
                subtitle="正在修改",
                job={"id": "job-1", "state": "coding"},
            )
            sink.emit("listening", turn=2)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["state"], "listening")
            self.assertNotIn("intent", payload)
            self.assertNotIn("subtitle", payload)
            self.assertEqual(payload["job"]["id"], "job-1")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class ProactiveTests(unittest.TestCase):
    def _settings(self, root: Path, **changes) -> Settings:
        values = {
            "vosk_model": root,
            "whisper_model": root / "model.bin",
            "state_file": root / "voice-state.json",
            "presence_file": root / "presence",
            "agenda_poll_seconds": 60,
            "daily_plan_time": "23:59",
        }
        values.update(changes)
        return Settings(**values)

    def test_meeting_reminder_is_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zone = ZoneInfo("Asia/Shanghai")
            now = datetime(2026, 7, 27, 10, 0, tzinfo=zone)
            meeting = Meeting("evt-1", "站会", now + timedelta(minutes=5))
            speaker = FakeSpeaker()
            scheduler = ProactiveScheduler(
                self._settings(root),
                FakeBrain(),
                speaker,
                FakeLark([meeting]),
            )
            scheduler.tick(now)
            scheduler.tick(now + timedelta(seconds=61))
            self.assertEqual(
                [text for text in speaker.spoken if "站会" in text],
                ["提醒你，站会将在5分钟后开始。"],
            )

    def test_return_to_desk_triggers_one_read_only_briefing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = self._settings(
                root,
                presence_absent_seconds=300,
                presence_cooldown_seconds=3600,
            )
            zone = ZoneInfo("Asia/Shanghai")
            now = datetime(2026, 7, 27, 8, 0, tzinfo=zone)
            settings.presence_file.write_text("absent")
            brain = FakeBrain()
            speaker = FakeSpeaker()
            scheduler = ProactiveScheduler(
                settings, brain, speaker, FakeLark()
            )
            scheduler.tick(now)
            settings.presence_file.write_text("present")
            scheduler.tick(now + timedelta(seconds=301))
            scheduler.tick(now + timedelta(seconds=400))
            self.assertEqual(len(brain.proactive_prompts), 1)
            self.assertFalse(brain.proactive_prompts[0][1])
            self.assertIn("just returned", brain.proactive_prompts[0][0])
            self.assertIn("完成前端", brain.proactive_prompts[0][0])

    def test_daily_planner_is_draft_only_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            brain = FakeBrain()
            scheduler = ProactiveScheduler(
                self._settings(root, daily_plan_time="09:10"),
                brain,
                FakeSpeaker(),
                FakeLark(),
            )
            now = datetime(
                2026, 7, 27, 9, 10, tzinfo=ZoneInfo("Asia/Shanghai")
            )
            scheduler.tick(now)
            self.assertEqual(len(brain.proactive_prompts), 1)
            prompt, allow_writes = brain.proactive_prompts[0]
            self.assertIn("Draft a proposed plan only", prompt)
            self.assertFalse(allow_writes)


class ConfigurationTests(unittest.TestCase):
    def test_models_are_required(self) -> None:
        with self.assertRaises(ConfigurationError):
            Settings.from_env({})

    def test_orangepi_zero3_is_the_default_hardware_profile(self) -> None:
        settings = Settings.from_env(
            {
                "BMO_VOSK_MODEL": "/opt/bmo/models/vosk",
                "BMO_WHISPER_MODEL": "/opt/bmo/models/ggml-base.bin",
            }
        )
        self.assertEqual(settings.hardware_profile, "orangepi-zero3-2gb")


if __name__ == "__main__":
    unittest.main()
