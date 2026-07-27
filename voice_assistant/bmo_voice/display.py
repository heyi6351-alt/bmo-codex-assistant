"""Tiny local web server for the HDMI BMO face."""

from __future__ import annotations

import argparse
import json
import logging
import os
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)
DEFAULT_WEB_ROOT = Path(__file__).resolve().parents[1] / "web"
DEFAULT_STATE_FILE = Path("/var/lib/bmo/voice-state.json")


def load_state(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"state": "idle", "updated_at": 0}
    if not isinstance(payload, dict):
        return {"state": "idle", "updated_at": 0}
    payload.setdefault("state", "idle")
    payload.setdefault("updated_at", 0)
    return payload


def make_handler(
    web_root: Path, state_file: Path
) -> type[SimpleHTTPRequestHandler]:
    class BmoDisplayHandler(SimpleHTTPRequestHandler):
        server_version = "BMODisplay/1.0"

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(web_root), **kwargs)

        def _json(self, payload: dict[str, Any], status: HTTPStatus) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            path = self.path.partition("?")[0]
            if path == "/api/state":
                self._json(load_state(state_file), HTTPStatus.OK)
                return
            if path == "/healthz":
                self._json({"ok": True}, HTTPStatus.OK)
                return
            super().do_GET()

        def log_message(self, fmt: str, *args: object) -> None:
            LOG.debug("%s - %s", self.address_string(), fmt % args)

    return BmoDisplayHandler


def build_server(
    host: str, port: int, web_root: Path, state_file: Path
) -> ThreadingHTTPServer:
    if not web_root.is_dir():
        raise FileNotFoundError(f"BMO web root does not exist: {web_root}")
    return ThreadingHTTPServer(
        (host, port),
        make_handler(web_root.resolve(), state_file.expanduser()),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the local BMO HDMI face")
    parser.add_argument(
        "--host", default=os.environ.get("BMO_DISPLAY_HOST", "127.0.0.1")
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("BMO_DISPLAY_PORT", "8765")),
    )
    parser.add_argument(
        "--web-root",
        type=Path,
        default=Path(os.environ.get("BMO_DISPLAY_ROOT", str(DEFAULT_WEB_ROOT))),
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path(
            os.environ.get("BMO_VOICE_STATE_FILE", str(DEFAULT_STATE_FILE))
        ),
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server = build_server(args.host, args.port, args.web_root, args.state_file)
    LOG.info("BMO face available at http://%s:%s", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
