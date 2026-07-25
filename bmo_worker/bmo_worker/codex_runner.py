"""Launch and supervise local Codex jobs without exposing a shell API."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Any

from .config import Settings
from .store import JobStore
from .verifier import verify_static_site


BASE_PROMPT = """\
You are BMO's coding specialist. BMO is supervising this job and you are working
inside one isolated project workspace.

Build the requested website completely in this workspace. Prefer a self-contained
static site using HTML, CSS and JavaScript unless this existing project already has
a framework. The finished project must include index.html and must work without
external CDNs, remote fonts, analytics, logins, secrets or network-only assets.

You may inspect, edit, build and test files inside this workspace. Do not access
anything outside it. Do not deploy, push Git commits, open accounts, use secrets,
purchase anything, or make host-level changes. Run appropriate local checks before
finishing and report exactly what you created.

BMO's request:
"""


class CodexRunner:
    def __init__(self, settings: Settings, store: JobStore):
        self.settings = settings
        self.store = store
        self._active: dict[str, asyncio.subprocess.Process] = {}

    @property
    def codex_available(self) -> bool:
        return bool(shutil.which(self.settings.codex_bin))

    async def run_job(self, job_id: str) -> None:
        job = self.store.get_job(job_id)
        if not job:
            return
        if await self._finish_if_cancelled(job_id):
            return
        if not self.settings.codex_isolated:
            self._fail(
                job_id,
                "Codex isolation is not configured",
                "run this worker inside an OS-isolated account or container",
            )
            return
        workspace = Path(job["workspace"])
        workspace.mkdir(parents=True, exist_ok=True)
        await self._ensure_git_repository(workspace)
        self._reject_workspace_links(workspace)

        previous_thread = None
        if job["action"] == "developer.website.continue":
            previous_thread = self.store.latest_thread_for_project(job["project_id"])

        prompt = BASE_PROMPT + job["goal"]
        self.store.update_job(job_id, state="coding")
        self.store.add_event(job_id, "coding", "BMO handed the project to Codex")
        if await self._finish_if_cancelled(job_id):
            return

        run = await self._run_codex(
            job_id=job_id,
            workspace=workspace,
            prompt=prompt,
            resume_thread=previous_thread,
        )
        if await self._finish_if_cancelled(job_id):
            return
        if run["exit_code"] != 0:
            self._fail(
                job_id,
                "Codex process failed",
                run.get("stderr") or f"exit code {run['exit_code']}",
            )
            return

        thread_id = run.get("thread_id") or previous_thread
        if thread_id:
            self.store.update_job(job_id, thread_id=thread_id)

        report: dict[str, Any] | None = None
        for attempt in range(self.settings.max_repair_attempts + 1):
            if await self._finish_if_cancelled(job_id):
                return
            self.store.update_job(job_id, state="verifying")
            self.store.add_event(
                job_id,
                "verifying",
                f"Playwright verification pass {attempt + 1}",
            )
            report = await verify_static_site(
                workspace, self.settings.artifacts_root / job_id
            )
            if await self._finish_if_cancelled(job_id):
                return
            self.store.update_job(
                job_id, verification_json=json.dumps(report, ensure_ascii=True)
            )
            if report["passed"]:
                result = run.get("last_message") or "Website completed and verified."
                self.store.update_job(job_id, state="done", result=result, error=None)
                self.store.add_event(
                    job_id,
                    "done",
                    "BMO verified the website on desktop and mobile",
                    {"screenshots": report.get("screenshots", [])},
                )
                return

            if report.get("infrastructure_error"):
                self._fail(job_id, "Verification infrastructure is unavailable", report)
                return
            if attempt >= self.settings.max_repair_attempts or not thread_id:
                break

            self.store.update_job(job_id, state="repairing")
            self.store.add_event(
                job_id,
                "repairing",
                "BMO returned browser failures to the same Codex thread",
                {"issues": report.get("issues", [])},
            )
            repair_prompt = (
                "BMO's deterministic browser verification found these problems:\n"
                + json.dumps(report.get("issues", []), ensure_ascii=True, indent=2)
                + "\nFix every issue inside the workspace, rerun local checks, and stop."
            )
            run = await self._run_codex(
                job_id=job_id,
                workspace=workspace,
                prompt=repair_prompt,
                resume_thread=thread_id,
            )
            if await self._finish_if_cancelled(job_id):
                return
            if run["exit_code"] != 0:
                self._fail(job_id, "Codex repair turn failed", run.get("stderr", ""))
                return

        self._fail(
            job_id,
            "Website did not pass browser verification",
            report or {"issues": ["unknown verification failure"]},
        )

    async def cancel(self, job_id: str) -> bool:
        changed = self.store.request_cancel(job_id)
        process = self._active.get(job_id)
        if process and process.returncode is None:
            await self._terminate_process_tree(process)
        return changed

    async def shutdown(self) -> None:
        for job_id in list(self._active):
            self.store.request_cancel(job_id)
            process = self._active.get(job_id)
            if process and process.returncode is None:
                await self._terminate_process_tree(process)

    async def _run_codex(
        self,
        *,
        job_id: str,
        workspace: Path,
        prompt: str,
        resume_thread: str | None,
    ) -> dict[str, Any]:
        if not self.codex_available:
            return {
                "exit_code": 127,
                "stderr": f"Codex executable not found: {self.settings.codex_bin}",
            }
        if self.store.is_cancel_requested(job_id):
            return {"exit_code": 130, "stderr": "job cancelled before Codex start"}

        command = self._build_command(workspace, resume_thread)

        child_env = self._sanitized_environment()
        creation: dict[str, Any] = {}
        if os.name == "nt":
            creation["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            creation["start_new_session"] = True

        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
            env=child_env,
            **creation,
        )
        self._active[job_id] = process
        assert process.stdin and process.stdout and process.stderr
        process.stdin.write(prompt.encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()

        state: dict[str, Any] = {
            "thread_id": resume_thread,
            "last_message": "",
            "stderr_lines": [],
        }

        async def read_stdout() -> None:
            while line := await process.stdout.readline():
                self._consume_json_event(job_id, line, state)

        async def read_stderr() -> None:
            while line := await process.stderr.readline():
                text = line.decode("utf-8", "replace").strip()
                if text:
                    state["stderr_lines"].append(text[:800])
                    state["stderr_lines"] = state["stderr_lines"][-20:]

        readers = asyncio.gather(read_stdout(), read_stderr())
        try:
            await asyncio.wait_for(
                process.wait(), timeout=self.settings.max_runtime_seconds
            )
            await readers
        except asyncio.TimeoutError:
            await self._terminate_process_tree(process)
            await readers
            self.store.add_event(job_id, "timeout", "Codex exceeded the job time limit")
            return {"exit_code": 124, "stderr": "job timed out"}
        finally:
            self._active.pop(job_id, None)

        return {
            "exit_code": int(process.returncode or 0),
            "thread_id": state.get("thread_id"),
            "last_message": state.get("last_message", ""),
            "stderr": "\n".join(state["stderr_lines"])[-4000:],
        }

    def _build_command(
        self, workspace: Path, resume_thread: str | None
    ) -> list[str]:
        if resume_thread:
            return [
                self.settings.codex_bin,
                "exec",
                "resume",
                "--json",
                "--ignore-user-config",
                "-c",
                'approval_policy="never"',
                "-c",
                'sandbox_mode="workspace-write"',
                resume_thread,
                "-",
            ]
        return [
            self.settings.codex_bin,
            "exec",
            "--json",
            "--ignore-user-config",
            "--sandbox",
            "workspace-write",
            "-c",
            'approval_policy="never"',
            "-C",
            str(workspace),
            "-",
        ]

    def _consume_json_event(
        self, job_id: str, raw_line: bytes, state: dict[str, Any]
    ) -> None:
        try:
            event = json.loads(raw_line.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            return
        event_type = str(event.get("type", "codex.event"))

        if event_type == "thread.started":
            thread_id = str(event.get("thread_id", "")).strip()
            if thread_id:
                state["thread_id"] = thread_id
                self.store.update_job(job_id, thread_id=thread_id)
            self.store.add_event(job_id, "codex_thread", "Codex thread started")
            return

        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        item_type = str(item.get("type", ""))
        if item_type == "agent_message":
            text = str(item.get("text", "")).strip()
            if text:
                state["last_message"] = text[-6000:]
                self.store.add_event(job_id, "codex_message", text[:1000])
        elif item_type == "command_execution":
            command = str(item.get("command", "")).strip()
            item_status = str(item.get("status", ""))
            self.store.add_event(
                job_id,
                "command",
                f"{item_status}: {command}"[:1000],
            )
        elif item_type == "file_change":
            self.store.add_event(job_id, "file_change", "Codex changed project files")
        elif event_type in {"turn.failed", "error"}:
            message = str(event.get("message") or event.get("error") or event_type)
            self.store.add_event(job_id, "codex_error", message[:1000])

    async def _ensure_git_repository(self, workspace: Path) -> None:
        if (workspace / ".git").exists():
            return
        process = await asyncio.create_subprocess_exec(
            "git",
            "init",
            cwd=workspace,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        if await process.wait() != 0:
            raise RuntimeError("could not initialize the project Git repository")

    @staticmethod
    def _reject_workspace_links(workspace: Path) -> None:
        if workspace.is_symlink():
            raise RuntimeError("project workspace must not be a symbolic link")
        if any(path.is_symlink() for path in workspace.rglob("*")):
            raise RuntimeError("project workspaces must not contain symbolic links")

    async def _finish_if_cancelled(self, job_id: str) -> bool:
        if not self.store.is_cancel_requested(job_id):
            return False
        self.store.update_job(job_id, state="cancelled")
        self.store.add_event(job_id, "cancelled", "BMO stopped the active job")
        return True

    def _fail(self, job_id: str, message: str, detail: Any) -> None:
        error = detail if isinstance(detail, str) else json.dumps(detail)
        self.store.update_job(job_id, state="failed", error=error[:6000])
        self.store.add_event(job_id, "failed", message)

    @staticmethod
    def _sanitized_environment() -> dict[str, str]:
        """Pass a small operational allowlist; the outer sandbox owns isolation."""

        allowed = {
            "APPDATA",
            "CODEX_HOME",
            "COMSPEC",
            "HOME",
            "LANG",
            "LC_ALL",
            "LOCALAPPDATA",
            "PATH",
            "PATHEXT",
            "PROGRAMFILES",
            "SYSTEMDRIVE",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "TMPDIR",
            "USERPROFILE",
            "WINDIR",
        }
        env = {name: value for name, value in os.environ.items() if name in allowed}
        env["NO_COLOR"] = "1"
        return env

    @staticmethod
    async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            if os.name == "nt":
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await killer.wait()
            else:
                os.killpg(process.pid, signal.SIGTERM)
            await asyncio.wait_for(process.wait(), timeout=5)
        except (ProcessLookupError, asyncio.TimeoutError):
            try:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
