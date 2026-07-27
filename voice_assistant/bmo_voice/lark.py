"""Small read-only Lark adapter for deterministic meeting reminders."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .config import Settings


class LarkError(RuntimeError):
    """Raised when a read-only Lark query fails."""


@dataclass(frozen=True)
class Meeting:
    event_id: str
    summary: str
    start: datetime
    end: datetime | None = None


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, dict):
        for key in ("data", "result"):
            if key in payload and isinstance(payload[key], (dict, list)):
                return _unwrap(payload[key])
    return payload


def _event_dicts(payload: Any) -> list[dict[str, Any]]:
    payload = _unwrap(payload)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("events", "items", "event_list"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        if "start_time" in payload and ("event_id" in payload or "summary" in payload):
            return [payload]
    return []


def parse_lark_time(value: Any, default_timezone: str) -> datetime:
    timezone_name = default_timezone
    if isinstance(value, dict):
        timezone_name = str(value.get("timezone") or default_timezone)
        value = (
            value.get("timestamp")
            or value.get("date_time")
            or value.get("time")
            or value.get("date")
        )
    if isinstance(value, (int, float)) or (
        isinstance(value, str) and value.strip().isdigit()
    ):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        return datetime.fromtimestamp(number, ZoneInfo(timezone_name))
    if isinstance(value, str):
        normalized = value.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
        return parsed
    raise ValueError(f"unsupported Lark time: {value!r}")


def parse_agenda(payload: Any, timezone_name: str) -> list[Meeting]:
    meetings: list[Meeting] = []
    for item in _event_dicts(payload):
        try:
            start = parse_lark_time(
                item.get("start_time") or item.get("start"), timezone_name
            )
        except (TypeError, ValueError, KeyError):
            continue
        raw_end = item.get("end_time") or item.get("end")
        try:
            end = parse_lark_time(raw_end, timezone_name) if raw_end else None
        except (TypeError, ValueError):
            end = None
        meetings.append(
            Meeting(
                event_id=str(item.get("event_id") or item.get("id") or start.timestamp()),
                summary=str(item.get("summary") or item.get("title") or "未命名日程"),
                start=start,
                end=end,
            )
        )
    return sorted(meetings, key=lambda meeting: meeting.start)


class LarkClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _json(self, arguments: list[str]) -> Any:
        result = subprocess.run(
            [self.settings.lark_cli_bin, *arguments, "--as", "user", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise LarkError(
                result.stderr.strip()[-1000:]
                or result.stdout.strip()[-1000:]
                or "lark-cli read failed"
            )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise LarkError("lark-cli returned invalid JSON") from exc

    def agenda(self, start: datetime, end: datetime) -> list[Meeting]:
        payload = self._json(
            [
                "calendar",
                "+agenda",
                "--start",
                start.isoformat(),
                "--end",
                end.isoformat(),
            ]
        )
        return parse_agenda(payload, self.settings.timezone)

    def incomplete_tasks(self, due_end: datetime | None = None) -> Any:
        arguments = [
            "task",
            "+get-my-tasks",
            "--complete=false",
            "--page-all",
        ]
        if due_end is not None:
            arguments += ["--due-end", due_end.isoformat()]
        return self._json(arguments)

    def messages(
        self, query: str, start: datetime, end: datetime
    ) -> Any:
        if not query.strip():
            return []
        return self._json(
            [
                "im",
                "+messages-search",
                "--query",
                query,
                "--start",
                start.isoformat(),
                "--end",
                end.isoformat(),
                "--page-size",
                "20",
                "--page-limit",
                "2",
                "--page-all",
                "--no-reactions",
            ]
        )
