"""State snapshot and optional Armada body-event bridge."""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)

FACE_BY_STATE = {
    "idle": "neutral",
    "wake": "surprised",
    "listening": "LISTENING",
    "interrupted": "LISTENING",
    "thinking": "THINKING",
    "speaking": "SPEAKING",
    "error": "sad",
}


class EventSink:
    def __init__(self, state_file: Path, body_url: str = ""):
        self.state_file = state_file
        self.body_url = body_url
        self._last: dict[str, Any] = {}

    def emit(self, state: str, **fields: Any) -> None:
        payload = {
            **self._last,
            **fields,
            "state": state,
            "updated_at": time.time(),
        }
        self._last = payload
        self._write_snapshot(payload)
        self._send_face(FACE_BY_STATE.get(state, "neutral"))

    def _write_snapshot(self, payload: dict[str, Any]) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.state_file)
        except OSError as exc:
            LOG.warning("could not write voice state snapshot: %s", exc)

    def _send_face(self, face: str) -> None:
        if not self.body_url:
            return
        body = json.dumps({"action": "face", "arg": face}).encode("utf-8")
        request = urllib.request.Request(
            self.body_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=1.5):
                pass
        except (OSError, urllib.error.URLError) as exc:
            LOG.debug("BMO body bridge unavailable: %s", exc)
