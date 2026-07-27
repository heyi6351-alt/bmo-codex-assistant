"""Pluggable local text-to-speech playback."""

from __future__ import annotations

import platform
import queue
import shlex
import shutil
import subprocess
import unicodedata
from collections import deque
from typing import Protocol

from .audio import pcm_rms
from .config import Settings


class InterruptAudio(Protocol):
    def read(self, timeout: float = 1.0) -> bytes: ...

    def replay(self, chunks) -> None: ...


class Speaker:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _command(self, text: str) -> list[str]:
        if self.settings.tts_command:
            return [
                part.replace("{text}", text)
                for part in shlex.split(self.settings.tts_command)
            ]
        if platform.system() == "Darwin" and shutil.which("say"):
            voice = "Tingting" if _contains_cjk(text) else "Samantha"
            return ["say", "-v", voice, text]
        if shutil.which("espeak-ng"):
            voice = "cmn" if _contains_cjk(text) else "en"
            return ["espeak-ng", "-v", voice, text]
        raise RuntimeError(
            "no TTS command is available; configure BMO_TTS_COMMAND"
        )

    def speak(self, text: str) -> None:
        result = subprocess.run(
            self._command(text),
            capture_output=True,
            text=True,
            timeout=max(30, len(text) // 3),
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "TTS playback failed")

    def speak_interruptible(
        self,
        text: str,
        microphone: InterruptAudio,
        *,
        energy_threshold: int,
        chunks_required: int,
    ) -> bool:
        """Play speech and stop when AEC-filtered microphone speech is detected."""

        process = subprocess.Popen(
            self._command(text),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        voiced: deque[bytes] = deque(maxlen=chunks_required)
        consecutive = 0
        try:
            while process.poll() is None:
                try:
                    chunk = microphone.read(timeout=0.1)
                except queue.Empty:
                    continue
                if pcm_rms(chunk) >= energy_threshold:
                    voiced.append(chunk)
                    consecutive += 1
                    if consecutive >= chunks_required:
                        process.terminate()
                        try:
                            process.wait(timeout=1.0)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=1.0)
                        microphone.replay(voiced)
                        return True
                else:
                    consecutive = 0
                    voiced.clear()
            stderr = process.communicate()[1]
            if process.returncode != 0:
                raise RuntimeError((stderr or "").strip() or "TTS playback failed")
            return False
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=1.0)


def _contains_cjk(text: str) -> bool:
    return any("CJK" in unicodedata.name(character, "") for character in text)
