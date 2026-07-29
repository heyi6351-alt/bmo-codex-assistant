"""Hardware-independent hands-free conversation state machine."""

from __future__ import annotations

import logging
from typing import Protocol

from .audio import Microphone
from .events import EventSink

LOG = logging.getLogger(__name__)

STOP_PHRASES = (
    "退出对话", "结束对话", "不用了", "再见", "休息吧",
    "stop listening", "goodbye", "that's all",
)


class WakeDetector(Protocol):
    def wait_for_wake(self, microphone: Microphone, timeout: float) -> bool: ...


class Transcriber(Protocol):
    def transcribe(
        self, microphone: Microphone, *, followup: bool = False
    ) -> str: ...


class Brain(Protocol):
    def ask(self, message: str) -> str: ...


class TextSpeaker(Protocol):
    def speak(self, text: str) -> None: ...


class ConversationController:
    def __init__(
        self,
        wake_detector: WakeDetector,
        transcriber: Transcriber,
        brain: Brain,
        speaker: TextSpeaker,
        events: EventSink,
        *,
        max_turns: int,
        barge_in_enabled: bool = False,
        barge_in_energy_threshold: int = 650,
        barge_in_chunks: int = 2,
    ):
        self.wake_detector = wake_detector
        self.transcriber = transcriber
        self.brain = brain
        self.speaker = speaker
        self.events = events
        self.max_turns = max_turns
        self.barge_in_enabled = barge_in_enabled
        self.barge_in_energy_threshold = barge_in_energy_threshold
        self.barge_in_chunks = barge_in_chunks

    def _safe_speak(self, text: str) -> None:
        """Speak without letting an audio-output failure kill the service.

        A dead or misconfigured speaker (common before the enclosure test) must
        degrade a single turn, not crash the whole loop into a restart storm.
        """

        try:
            self.speaker.speak(text)
        except Exception:
            LOG.exception("speech playback failed")

    def _speak_reply(self, text: str, microphone: Microphone) -> bool:
        interruptible = getattr(self.speaker, "speak_interruptible", None)
        try:
            if self.barge_in_enabled and callable(interruptible):
                return bool(
                    interruptible(
                        text,
                        microphone,
                        energy_threshold=self.barge_in_energy_threshold,
                        chunks_required=self.barge_in_chunks,
                    )
                )
            self.speaker.speak(text)
        except Exception:
            LOG.exception("reply playback failed")
        return False

    def poll_and_run(self, microphone: Microphone, wake_timeout: float) -> int:
        """Poll for wake so proactive jobs can run between microphone windows."""

        self.events.emit("idle")
        if not self.wake_detector.wait_for_wake(microphone, wake_timeout):
            return 0
        self.events.emit("wake")
        self._safe_speak("我在")
        microphone.clear()

        completed = 0
        for turn in range(self.max_turns):
            self.events.emit("listening", turn=turn + 1)
            try:
                transcript = self.transcriber.transcribe(
                    microphone, followup=turn > 0
                ).strip()
            except Exception:
                LOG.exception("transcription failed")
                self.events.emit("error", error="transcription failed")
                self._safe_speak("抱歉，我刚才没有听清")
                microphone.clear()
                break
            if not transcript:
                break
            normalized = transcript.casefold()
            if any(phrase in normalized for phrase in STOP_PHRASES):
                self._safe_speak("好的，需要我时再叫我")
                microphone.clear()
                break

            self.events.emit("thinking", transcript=transcript)
            try:
                reply = self.brain.ask(transcript)
            except Exception:
                LOG.exception("assistant turn failed")
                # Keep internal exception details (paths, env values) out of
                # the LAN-served state file; logs carry the full traceback.
                self.events.emit("error", error="assistant turn failed")
                self._safe_speak("抱歉，我刚才没有处理成功")
                microphone.clear()
                break

            if getattr(self.brain, "awaiting_confirmation", False):
                self.events.emit(
                    "confirmation", transcript=transcript, reply=reply
                )
            else:
                self.events.emit("speaking", transcript=transcript, reply=reply)
            interrupted = self._speak_reply(reply, microphone)
            if interrupted:
                self.events.emit(
                    "interrupted", transcript=transcript, reply=reply
                )
            else:
                microphone.clear()
            completed += 1

        self.events.emit("idle", turn=0)
        return completed

    def run_session(self, microphone: Microphone) -> int:
        """Compatibility helper for tests and one-shot callers."""

        return self.poll_and_run(microphone, 24 * 60 * 60)
