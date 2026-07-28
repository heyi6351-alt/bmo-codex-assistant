"""Environment-backed configuration for the Codex desk assistant."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class ConfigurationError(RuntimeError):
    """Raised when the voice service cannot start safely."""


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be positive")
    return value


def _positive_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be positive")
    return value


def _boolean(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name, "1" if default else "0").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true/false or 1/0")


@dataclass(frozen=True)
class Settings:
    vosk_model: Path
    whisper_model: Path = Path("/opt/bmo/models/ggml-small.bin")
    hardware_profile: str = "orangepi-zero3-2gb"
    wake_phrases: tuple[str, ...] = (
        "BMO",
        "哔某",
        "比莫",
        "你好BMO",
        "Hey BMO",
    )
    audio_device: str | int | None = None
    sample_rate: int = 16_000
    block_size: int = 4_000
    energy_threshold: int = 350
    speech_start_timeout: float = 5.0
    silence_timeout: float = 1.1
    max_utterance_seconds: float = 30.0
    followup_timeout: float = 8.0
    max_conversation_turns: int = 6
    wake_poll_seconds: float = 1.0
    barge_in_enabled: bool = True
    barge_in_energy_threshold: int = 650
    barge_in_chunks: int = 2

    whisper_bin: str = "whisper-cli"
    codex_bin: str = "codex"
    codex_workspace: Path = Path("/opt/bmo/voice_assistant/codex_workspace")
    codex_project_root: Path = Path("/var/lib/bmo/projects")
    codex_session_file: Path = Path("/var/lib/bmo/codex-session.json")
    codex_timeout_seconds: int = 300
    codex_model: str = ""
    codex_profile: str = ""

    lark_cli_bin: str = "lark-cli"
    timezone: str = "Asia/Shanghai"
    agenda_poll_seconds: int = 60
    meeting_reminder_minutes: int = 5
    presence_file: Path = Path("/run/bmo/presence")
    presence_absent_seconds: int = 300
    presence_cooldown_seconds: int = 3600
    daily_plan_time: str = "09:10"
    autoplan_writes: bool = False
    work_message_query: str = ""

    tts_command: str = ""
    body_url: str = "http://127.0.0.1:8099/v1/bmo"
    state_file: Path = Path("/var/lib/bmo/voice-state.json")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        raw_vosk = env.get("BMO_VOSK_MODEL", "").strip()
        raw_whisper = env.get("BMO_WHISPER_MODEL", "").strip()
        if not raw_vosk:
            raise ConfigurationError(
                "BMO_VOSK_MODEL must point to an unpacked Vosk wake model"
            )
        if not raw_whisper:
            raise ConfigurationError(
                "BMO_WHISPER_MODEL must point to a multilingual whisper.cpp model"
            )

        phrases = tuple(
            phrase.strip()
            for phrase in env.get(
                "BMO_WAKE_PHRASES", "BMO,哔某,比莫,你好BMO,Hey BMO"
            ).split(",")
            if phrase.strip()
        )
        if not phrases:
            raise ConfigurationError("BMO_WAKE_PHRASES must contain at least one phrase")

        raw_device = env.get("BMO_AUDIO_DEVICE", "").strip()
        device: str | int | None
        if not raw_device:
            device = None
        elif raw_device.isdecimal():
            device = int(raw_device)
        else:
            device = raw_device

        return cls(
            vosk_model=Path(raw_vosk).expanduser(),
            whisper_model=Path(raw_whisper).expanduser(),
            hardware_profile=env.get(
                "BMO_HARDWARE_PROFILE", "orangepi-zero3-2gb"
            ).strip()
            or "orangepi-zero3-2gb",
            wake_phrases=phrases,
            audio_device=device,
            sample_rate=_positive_int(env, "BMO_SAMPLE_RATE", 16_000),
            block_size=_positive_int(env, "BMO_BLOCK_SIZE", 4_000),
            energy_threshold=_positive_int(env, "BMO_ENERGY_THRESHOLD", 350),
            speech_start_timeout=_positive_float(
                env, "BMO_SPEECH_START_TIMEOUT", 5.0
            ),
            silence_timeout=_positive_float(env, "BMO_SILENCE_TIMEOUT", 1.1),
            max_utterance_seconds=_positive_float(
                env, "BMO_MAX_UTTERANCE_SECONDS", 30.0
            ),
            followup_timeout=_positive_float(env, "BMO_FOLLOWUP_TIMEOUT", 8.0),
            max_conversation_turns=_positive_int(
                env, "BMO_MAX_CONVERSATION_TURNS", 6
            ),
            wake_poll_seconds=_positive_float(env, "BMO_WAKE_POLL_SECONDS", 1.0),
            barge_in_enabled=_boolean(env, "BMO_BARGE_IN_ENABLED", True),
            barge_in_energy_threshold=_positive_int(
                env, "BMO_BARGE_IN_ENERGY_THRESHOLD", 650
            ),
            barge_in_chunks=_positive_int(env, "BMO_BARGE_IN_CHUNKS", 2),
            whisper_bin=env.get("BMO_WHISPER_BIN", "whisper-cli").strip()
            or "whisper-cli",
            codex_bin=env.get("BMO_CODEX_BIN", "codex").strip() or "codex",
            codex_workspace=Path(
                env.get(
                    "BMO_CODEX_WORKSPACE",
                    "/opt/bmo/voice_assistant/codex_workspace",
                )
            ).expanduser(),
            codex_project_root=Path(
                env.get("BMO_CODEX_PROJECT_ROOT", "/var/lib/bmo/projects")
            ).expanduser(),
            codex_session_file=Path(
                env.get(
                    "BMO_CODEX_SESSION_FILE",
                    "/var/lib/bmo/codex-session.json",
                )
            ).expanduser(),
            codex_timeout_seconds=_positive_int(
                env, "BMO_CODEX_TIMEOUT_SECONDS", 300
            ),
            codex_model=env.get("BMO_CODEX_MODEL", "").strip(),
            codex_profile=env.get("BMO_CODEX_PROFILE", "").strip(),
            lark_cli_bin=env.get("BMO_LARK_CLI_BIN", "lark-cli").strip()
            or "lark-cli",
            timezone=env.get("BMO_TIMEZONE", "Asia/Shanghai").strip()
            or "Asia/Shanghai",
            agenda_poll_seconds=_positive_int(
                env, "BMO_AGENDA_POLL_SECONDS", 60
            ),
            meeting_reminder_minutes=_positive_int(
                env, "BMO_MEETING_REMINDER_MINUTES", 5
            ),
            presence_file=Path(
                env.get("BMO_PRESENCE_FILE", "/run/bmo/presence")
            ).expanduser(),
            presence_absent_seconds=_positive_int(
                env, "BMO_PRESENCE_ABSENT_SECONDS", 300
            ),
            presence_cooldown_seconds=_positive_int(
                env, "BMO_PRESENCE_COOLDOWN_SECONDS", 3600
            ),
            daily_plan_time=env.get("BMO_DAILY_PLAN_TIME", "09:10").strip(),
            autoplan_writes=_boolean(env, "BMO_AUTOPLAN_WRITES", False),
            work_message_query=env.get("BMO_WORK_MESSAGE_QUERY", "").strip(),
            tts_command=env.get("BMO_TTS_COMMAND", "").strip(),
            body_url=env.get(
                "BMO_BODY_URL", "http://127.0.0.1:8099/v1/bmo"
            ).strip(),
            state_file=Path(
                env.get("BMO_VOICE_STATE_FILE", "/var/lib/bmo/voice-state.json")
            ).expanduser(),
        )

    def validate_runtime(self) -> None:
        if not self.vosk_model.is_dir():
            raise ConfigurationError(
                f"Vosk model directory does not exist: {self.vosk_model}"
            )
        if not self.whisper_model.is_file():
            raise ConfigurationError(
                f"whisper.cpp model does not exist: {self.whisper_model}"
            )
        if self.sample_rate != 16_000:
            raise ConfigurationError("voice capture currently requires 16000 Hz audio")
        try:
            hour, minute = (int(part) for part in self.daily_plan_time.split(":"))
        except (TypeError, ValueError) as exc:
            raise ConfigurationError("BMO_DAILY_PLAN_TIME must be HH:MM") from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ConfigurationError("BMO_DAILY_PLAN_TIME must be a valid HH:MM")
