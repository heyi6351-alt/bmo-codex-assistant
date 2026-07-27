"""Microphone capture, offline wake detection, and bilingual whisper.cpp ASR."""

from __future__ import annotations

import json
import logging
import math
import queue
import subprocess
import tempfile
import time
import wave
from array import array
from collections import deque
from pathlib import Path
from typing import Iterable

from .config import Settings

LOG = logging.getLogger(__name__)


def normalize_speech(text: str) -> str:
    """Normalize ASR output for wake/stop phrase matching."""

    return "".join(text.casefold().split()).replace("，", "").replace("。", "")


def contains_phrase(text: str, phrases: Iterable[str]) -> bool:
    normalized = normalize_speech(text)
    return any(normalize_speech(phrase) in normalized for phrase in phrases)


def pcm_rms(chunk: bytes) -> float:
    """Return RMS energy for little-endian signed 16-bit PCM."""

    if not chunk:
        return 0.0
    samples = array("h")
    samples.frombytes(chunk)
    if not samples:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


class Microphone:
    """One PortAudio input stream shared by wake and command phases."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._chunks: queue.Queue[bytes] = queue.Queue(maxsize=32)
        self._replay: deque[bytes] = deque()
        self._stream = None

    def __enter__(self) -> "Microphone":
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError(
                "sounddevice is not installed; install voice_assistant requirements"
            ) from exc

        def callback(indata, _frames, _time_info, status) -> None:
            if status:
                LOG.warning("microphone status: %s", status)
            try:
                self._chunks.put_nowait(bytes(indata))
            except queue.Full:
                try:
                    self._chunks.get_nowait()
                except queue.Empty:
                    pass
                self._chunks.put_nowait(bytes(indata))

        self._stream = sd.RawInputStream(
            samplerate=self.settings.sample_rate,
            blocksize=self.settings.block_size,
            device=self.settings.audio_device,
            dtype="int16",
            channels=1,
            callback=callback,
        )
        self._stream.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def read(self, timeout: float = 1.0) -> bytes:
        if self._replay:
            return self._replay.popleft()
        return self._chunks.get(timeout=timeout)

    def replay(self, chunks: Iterable[bytes]) -> None:
        """Return already-read audio to the front of the consumer stream."""

        buffered = list(chunks)
        self._replay.extendleft(reversed(buffered))

    def clear(self) -> None:
        self._replay.clear()
        while True:
            try:
                self._chunks.get_nowait()
            except queue.Empty:
                return


class VoskWakeDetector:
    """Low-power offline wake phrase detector."""

    def __init__(self, settings: Settings):
        self.settings = settings
        try:
            from vosk import KaldiRecognizer, Model, SetLogLevel
        except ImportError as exc:
            raise RuntimeError(
                "vosk is not installed; install voice_assistant requirements"
            ) from exc
        SetLogLevel(-1)
        self._recognizer_class = KaldiRecognizer
        self._model = Model(str(settings.vosk_model))
        self._recognizer = self._new_recognizer()

    def _new_recognizer(self):
        return self._recognizer_class(self._model, self.settings.sample_rate)

    @staticmethod
    def _field(payload: str, name: str) -> str:
        try:
            value = json.loads(payload).get(name, "")
        except (json.JSONDecodeError, AttributeError):
            return ""
        return str(value or "")

    def wait_for_wake(self, microphone: Microphone, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                chunk = microphone.read(
                    timeout=min(0.5, max(0.01, deadline - time.monotonic()))
                )
            except queue.Empty:
                continue
            if self._recognizer.AcceptWaveform(chunk):
                text = self._field(self._recognizer.Result(), "text")
            else:
                text = self._field(self._recognizer.PartialResult(), "partial")
            if contains_phrase(text, self.settings.wake_phrases):
                self._recognizer = self._new_recognizer()
                microphone.clear()
                return True
        return False


class WhisperCppTranscriber:
    """Record one utterance and let multilingual whisper.cpp detect zh/en."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def _capture(self, microphone: Microphone, *, followup: bool) -> bytes:
        started_at = time.monotonic()
        speech_started_at: float | None = None
        last_voice_at: float | None = None
        chunks: list[bytes] = []
        start_timeout = (
            self.settings.followup_timeout
            if followup
            else self.settings.speech_start_timeout
        )

        while True:
            now = time.monotonic()
            if speech_started_at is None and now - started_at >= start_timeout:
                return b""
            if (
                speech_started_at is not None
                and now - speech_started_at >= self.settings.max_utterance_seconds
            ):
                break
            if (
                last_voice_at is not None
                and now - last_voice_at >= self.settings.silence_timeout
            ):
                break
            try:
                chunk = microphone.read(timeout=0.25)
            except queue.Empty:
                continue
            energy = pcm_rms(chunk)
            if energy >= self.settings.energy_threshold:
                if speech_started_at is None:
                    speech_started_at = now
                last_voice_at = now
            if speech_started_at is not None:
                chunks.append(chunk)
        return b"".join(chunks)

    def transcribe(self, microphone: Microphone, *, followup: bool = False) -> str:
        pcm = self._capture(microphone, followup=followup)
        if not pcm:
            return ""
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
                temporary_path = Path(handle.name)
            with wave.open(str(temporary_path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(self.settings.sample_rate)
                wav.writeframes(pcm)
            result = subprocess.run(
                [
                    self.settings.whisper_bin,
                    "-m",
                    str(self.settings.whisper_model),
                    "-f",
                    str(temporary_path),
                    "-l",
                    "auto",
                    "-np",
                    "-nt",
                ],
                capture_output=True,
                text=True,
                timeout=max(30, int(self.settings.max_utterance_seconds * 4)),
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    result.stderr.strip()[-1000:] or "whisper.cpp failed"
                )
            return " ".join(result.stdout.split()).strip()
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
