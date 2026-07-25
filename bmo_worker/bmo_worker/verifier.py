"""Deterministic, network-isolated browser verification for static websites."""

from __future__ import annotations

import asyncio
import functools
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format: str, *args: object) -> None:
        del args


async def verify_static_site(
    workspace: Path, artifact_dir: Path
) -> dict[str, Any]:
    linked = [
        path.relative_to(workspace).as_posix()
        for path in workspace.rglob("*")
        if path.is_symlink()
    ]
    if linked:
        return {
            "passed": False,
            "issues": ["project contains symbolic links, which are not allowed"],
            "screenshots": [],
        }

    index = workspace / "index.html"
    if not index.is_file():
        return {
            "passed": False,
            "issues": ["index.html is missing"],
            "screenshots": [],
        }

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {
            "passed": False,
            "infrastructure_error": True,
            "issues": [
                "Playwright is not installed; install bmo_worker requirements and Chromium"
            ],
            "screenshots": [],
        }

    handler = functools.partial(_QuietHandler, directory=str(workspace))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    port = int(server.server_address[1])
    url = f"http://127.0.0.1:{port}/"

    artifact_dir.mkdir(parents=True, exist_ok=True)
    issues: list[str] = []
    blocked: list[str] = []
    screenshots: list[str] = []

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                for name, viewport in (
                    ("desktop", {"width": 1440, "height": 1000}),
                    ("mobile", {"width": 390, "height": 844}),
                ):
                    context = await browser.new_context(
                        viewport=viewport,
                        accept_downloads=False,
                        service_workers="block",
                    )
                    page = await context.new_page()

                    async def restrict_network(route: Any) -> None:
                        requested = urlparse(route.request.url)
                        if (
                            requested.scheme == "http"
                            and requested.hostname == "127.0.0.1"
                            and requested.port == port
                        ):
                            await route.continue_()
                        else:
                            blocked.append(route.request.url[:300])
                            await route.abort()

                    await page.route("**/*", restrict_network)
                    page.on(
                        "console",
                        lambda message: issues.append("console error detected")
                        if message.type == "error"
                        else None,
                    )
                    page.on(
                        "pageerror",
                        lambda _error: issues.append("page error detected"),
                    )
                    page.on(
                        "popup",
                        lambda popup: asyncio.create_task(popup.close()),
                    )
                    response = await page.goto(
                        url, wait_until="domcontentloaded", timeout=20_000
                    )
                    await page.wait_for_timeout(400)
                    if response is None or response.status >= 400:
                        status = response.status if response else "no response"
                        issues.append(f"preview returned {status}")

                    body = (await page.locator("body").inner_text()).strip()
                    if len(body) < 20:
                        issues.append(f"{name} page has almost no visible content")

                    screenshot = artifact_dir / f"{name}.png"
                    await page.screenshot(path=str(screenshot), full_page=True)
                    screenshots.append(screenshot.name)
                    await context.close()
            finally:
                await browser.close()
    except Exception as exc:
        issues.append(f"browser verification failed: {exc}"[:700])
    finally:
        server.shutdown()
        server.server_close()
        await asyncio.to_thread(server_thread.join, 2)

    if blocked:
        issues.append(
            "site attempted external network requests; bundle assets locally: "
            + ", ".join(dict.fromkeys(blocked[:4]))
        )

    return {
        "passed": not issues,
        "issues": issues,
        "blocked_requests": list(dict.fromkeys(blocked))[:20],
        "screenshots": screenshots,
        "preview_file": "index.html",
    }
