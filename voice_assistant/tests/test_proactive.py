from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bmo_voice.config import Settings
from bmo_voice.events import EventSink
from bmo_voice.proactive import ProactiveScheduler


class FakeBrain:
    def __init__(self):
        self.prompts: list[tuple[str, bool]] = []

    def proactive(self, prompt: str, *, allow_writes: bool = False) -> str:
        self.prompts.append((prompt, allow_writes))
        return "简报"


class FakeSpeaker:
    def __init__(self):
        self.spoken: list[str] = []

    def speak(self, text: str) -> None:
        self.spoken.append(text)


class FakeLark:
    def __init__(self, meetings=None):
        self.meetings = meetings or []

    def agenda(self, _start, _end):
        return self.meetings

    def incomplete_tasks(self, _due_end=None):
        return {"items": []}

    def messages(self, _query, _start, _end):
        return {"items": []}


class RecordingSink:
    def __init__(self):
        self.events: list[dict] = []

    def emit(self, state: str, **fields):
        self.events.append({"state": state, **fields})


class ProactiveSchedulerTests(unittest.TestCase):
    def _settings(self, root: Path, **changes) -> Settings:
        values = {
            "vosk_model": root,
            "whisper_model": root / "model.bin",
            "state_file": root / "voice-state.json",
            "presence_file": root / "presence",
            "dnd_file": root / "dnd",
            "agenda_poll_seconds": 60,
            "daily_plan_time": "23:59",
            "quiet_hours_start": "22:30",
            "quiet_hours_end": "08:00",
        }
        values.update(changes)
        return Settings(**values)

    def _scheduler(self, root: Path, **changes):
        self.sink = RecordingSink()
        self.speaker = FakeSpeaker()
        self.lark = FakeLark()
        self.brain = FakeBrain()
        return ProactiveScheduler(
            self._settings(root, **changes),
            self.brain,
            self.speaker,
            self.lark,
            self.sink,
            clock=lambda: 1_000_000.0,
        )

    def test_dnd_file_suppresses_meeting_reminder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zone = ZoneInfo("Asia/Shanghai")
            now = datetime(2026, 7, 27, 10, 0, tzinfo=zone)
            settings = self._settings(root)
            settings.dnd_file.write_text("true", encoding="utf-8")
            from bmo_voice.lark import Meeting

            scheduler = self._scheduler(
                root, agenda_poll_seconds=0, meeting_reminder_minutes=10
            )
            scheduler.lark = FakeLark([Meeting("evt-1", "站会", now + timedelta(minutes=5))])
            scheduler.tick(now)
            self.assertEqual(self.speaker.spoken, [])

    def test_quiet_hours_suppresses_morning_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zone = ZoneInfo("Asia/Shanghai")
            now = datetime(2026, 7, 27, 23, 0, tzinfo=zone)
            scheduler = self._scheduler(
                root, daily_plan_time="09:10", autoplan_writes=False
            )
            scheduler.tick(now)
            self.assertEqual(self.speaker.spoken, [])
            self.assertEqual(len(self.brain.prompts), 0)

    def test_today_suppression_blocks_reminders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zone = ZoneInfo("Asia/Shanghai")
            now = datetime(2026, 7, 27, 10, 0, tzinfo=zone)
            from bmo_voice.lark import Meeting

            scheduler = self._scheduler(
                root, agenda_poll_seconds=0, meeting_reminder_minutes=10
            )
            scheduler.lark = FakeLark([Meeting("evt-1", "站会", now + timedelta(minutes=5))])
            scheduler.suppress_today(now)
            scheduler.tick(now)
            self.assertEqual(self.speaker.spoken, [])
            self.assertEqual(
                scheduler.state_path.stat().st_mode & 0o777,
                0o600,
            )

    def test_snooze_blocks_reminders_until_expired(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zone = ZoneInfo("Asia/Shanghai")
            now = datetime(2026, 7, 27, 10, 0, tzinfo=zone)
            from bmo_voice.lark import Meeting

            scheduler = self._scheduler(
                root, agenda_poll_seconds=0, meeting_reminder_minutes=10
            )
            scheduler.lark = FakeLark([Meeting("evt-1", "站会", now + timedelta(minutes=5))])
            scheduler.snooze(10, now)
            scheduler.tick(now)
            self.assertEqual(self.speaker.spoken, [])

    def test_meeting_reminder_emits_reminder_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zone = ZoneInfo("Asia/Shanghai")
            now = datetime(2026, 7, 27, 10, 0, tzinfo=zone)
            from bmo_voice.lark import Meeting

            scheduler = self._scheduler(
                root, agenda_poll_seconds=0, meeting_reminder_minutes=10
            )
            scheduler.lark = FakeLark([Meeting("evt-1", "站会", now + timedelta(minutes=5))])
            scheduler.tick(now)
            self.assertTrue(any("站会" in s for s in self.speaker.spoken))
            self.assertTrue(any(e["state"] == "reminder" for e in self.sink.events))
            self.assertEqual(self.sink.events[-1]["state"], "idle")

    def test_daily_plan_emits_planning_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zone = ZoneInfo("Asia/Shanghai")
            now = datetime(2026, 7, 27, 9, 10, tzinfo=zone)
            scheduler = self._scheduler(
                root, daily_plan_time="09:10", autoplan_writes=False
            )
            scheduler.tick(now)
            self.assertEqual(len(self.brain.prompts), 1)
            self.assertFalse(self.brain.prompts[0][1])
            self.assertTrue(any(e["state"] == "planning" for e in self.sink.events))
            self.assertEqual(self.sink.events[-1]["state"], "idle")

    def test_resume_reminders_clears_suppression(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scheduler = self._scheduler(root)
            scheduler.suppress_today()
            self.assertTrue(scheduler._is_suppressed(datetime.now(ZoneInfo("Asia/Shanghai"))))
            scheduler.resume_reminders()
            self.assertFalse(scheduler._is_suppressed(datetime.now(ZoneInfo("Asia/Shanghai"))))

    def test_failed_arrival_briefing_is_contained(self):
        class FailingBrain:
            def __init__(self):
                self.calls = 0

            def proactive(self, _prompt, *, allow_writes=False):
                self.calls += 1
                raise RuntimeError("codex down")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zone = ZoneInfo("Asia/Shanghai")
            now = datetime(2026, 7, 27, 10, 0, tzinfo=zone)
            scheduler = self._scheduler(
                root, presence_absent_seconds=300, presence_cooldown_seconds=3600
            )
            scheduler.brain = FailingBrain()
            scheduler.settings.presence_file.write_text("absent", encoding="utf-8")
            scheduler.tick(now)
            scheduler.settings.presence_file.write_text("present", encoding="utf-8")
            # The failing briefing must not escape tick() and the cooldown
            # timestamp must still be recorded.
            scheduler.tick(now + timedelta(seconds=301))
            self.assertEqual(scheduler.brain.calls, 1)
            self.assertIn("arrival_briefing_at", scheduler.state)


if __name__ == "__main__":
    unittest.main()
