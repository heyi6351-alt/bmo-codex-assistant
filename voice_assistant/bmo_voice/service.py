"""Command-line entry point for the always-on Codex desk assistant."""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys

from .audio import Microphone, VoskWakeDetector, WhisperCppTranscriber
from .brain import AssistantBrain
from .codex import CodexBrain
from .config import ConfigurationError, Settings
from .core import ConversationController
from .events import EventSink
from .jobs import CodexJobManager
from .lark import LarkClient
from .proactive import ProactiveScheduler
from .tts import Speaker

LOG = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Hands-free bilingual Codex desk assistant for BMO"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate configuration and dependencies without opening the microphone",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="exit after one wake/conversation session",
    )
    parser.add_argument(
        "--no-body",
        action="store_true",
        help="do not POST face states to the Armada BMO bridge",
    )
    parser.add_argument(
        "--list-audio-devices",
        action="store_true",
        help="list PortAudio devices as JSON without requiring model configuration",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def list_audio_devices() -> int:
    try:
        import sounddevice as sd
    except ImportError:
        print("sounddevice is not installed", file=sys.stderr)
        return 2
    devices = []
    for index, device in enumerate(sd.query_devices()):
        devices.append(
            {
                "index": index,
                "name": device["name"],
                "inputs": int(device["max_input_channels"]),
                "outputs": int(device["max_output_channels"]),
                "default_sample_rate": int(device["default_samplerate"]),
            }
        )
    print(json.dumps(devices, ensure_ascii=False, indent=2))
    return 0


def runtime_check(settings: Settings) -> list[str]:
    settings.validate_runtime()
    problems: list[str] = []
    for executable, label in (
        (settings.codex_bin, "Codex"),
        (settings.whisper_bin, "whisper.cpp"),
        (settings.lark_cli_bin, "lark-cli"),
    ):
        if not shutil.which(executable):
            problems.append(f"{label} executable not found: {executable}")
    if not settings.tts_command:
        if sys.platform == "darwin":
            if not shutil.which("say"):
                problems.append("macOS say command was not found")
        elif not shutil.which("espeak-ng"):
            problems.append("install espeak-ng or set BMO_TTS_COMMAND")
    try:
        import sounddevice as sd
    except ImportError:
        problems.append("sounddevice is not installed")
    else:
        try:
            sd.query_devices(settings.audio_device, "input")
        except (ValueError, TypeError):
            problems.append(
                f"audio input device was not found: {settings.audio_device!r}; "
                "run --list-audio-devices"
            )
    try:
        __import__("vosk")
    except ImportError:
        problems.append("vosk is not installed")
    return problems


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.list_audio_devices:
        return list_audio_devices()
    try:
        settings = Settings.from_env()
        problems = runtime_check(settings)
    except ConfigurationError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    if problems:
        for problem in problems:
            print(f"not ready: {problem}", file=sys.stderr)
        return 2
    if args.check:
        print(
            "BMO configuration: OK "
            f"(hardware={settings.hardware_profile}, audio={settings.audio_device!r})"
        )
        return 0

    body_url = "" if args.no_body else settings.body_url
    events = EventSink(settings.state_file, body_url)
    speaker = Speaker(settings)
    codex_brain = CodexBrain(settings)
    job_manager = CodexJobManager(settings, events)
    lark_client = LarkClient(settings)
    proactive = ProactiveScheduler(
        settings, codex_brain, speaker, lark_client, events
    )
    assistant = AssistantBrain(
        settings, events, codex_brain, job_manager, proactive
    )
    controller = ConversationController(
        VoskWakeDetector(settings),
        WhisperCppTranscriber(settings),
        assistant,
        speaker,
        events,
        max_turns=settings.max_conversation_turns,
        barge_in_enabled=settings.barge_in_enabled,
        barge_in_energy_threshold=settings.barge_in_energy_threshold,
        barge_in_chunks=settings.barge_in_chunks,
    )

    try:
        with Microphone(settings) as microphone:
            while True:
                try:
                    proactive.tick()
                except Exception:
                    LOG.exception("proactive scheduler tick failed")
                for message in assistant.poll_notifications():
                    if "已完成" in message:
                        state = "done"
                    elif "已取消" in message:
                        state = "cancelled"
                    else:
                        state = "failed"
                    try:
                        events.emit(state, subtitle=message[:300])
                        speaker.speak(message)
                    except Exception:
                        LOG.exception("job notification speech failed")
                    finally:
                        events.emit("idle")
                microphone.clear()
                if args.once:
                    controller.run_session(microphone)
                    return 0
                controller.poll_and_run(microphone, settings.wake_poll_seconds)
    except KeyboardInterrupt:
        events.emit("idle")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
