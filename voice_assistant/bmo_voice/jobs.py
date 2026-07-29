"""Non-blocking, persisted Codex coding jobs."""

from __future__ import annotations

import json
import logging
import os
import queue
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from .config import Settings
from .intent import route_request
from .publish import (
    GitHubPublisher,
    PreparedPublish,
    PublishError,
)

LOG = logging.getLogger(__name__)


class JobEventSink(Protocol):
    def emit(self, state: str, **fields: Any) -> None: ...


@dataclass
class CodexJob:
    job_id: str
    request: str
    project: str
    state: str
    created_at: float
    updated_at: float
    result: str = ""
    error: str = ""
    request_id: str = ""
    confirmed: bool = False
    branch: str = ""
    base_branch: str = ""
    base_commit: str = ""
    changed_files: list[str] | None = None
    change_digest: str = ""
    commit_sha: str = ""
    pr_number: int | None = None
    pr_url: str = ""
    publish_request_id: str = ""
    publish_confirmed_at: float = 0
    publish_offer_expires_at: float = 0

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CodexJob":
        return cls(
            job_id=str(payload["job_id"]),
            request=str(payload.get("request", "")),
            project=str(payload.get("project", "")),
            state=str(payload.get("state", "interrupted")),
            created_at=float(payload.get("created_at", 0)),
            updated_at=float(payload.get("updated_at", 0)),
            result=str(payload.get("result", "")),
            error=str(payload.get("error", "")),
            request_id=str(payload.get("request_id", "")),
            confirmed=bool(payload.get("confirmed", False)),
            branch=str(payload.get("branch", "")),
            base_branch=str(payload.get("base_branch", "")),
            base_commit=str(payload.get("base_commit", "")),
            changed_files=[
                str(path)
                for path in (payload.get("changed_files") or [])
                if isinstance(path, str)
            ],
            change_digest=str(payload.get("change_digest", "")),
            commit_sha=str(payload.get("commit_sha", "")),
            pr_number=(
                int(payload["pr_number"])
                if payload.get("pr_number") is not None
                else None
            ),
            pr_url=str(payload.get("pr_url", "")),
            publish_request_id=str(payload.get("publish_request_id", "")),
            publish_confirmed_at=float(
                payload.get("publish_confirmed_at", 0)
            ),
            publish_offer_expires_at=float(
                payload.get("publish_offer_expires_at", 0)
            ),
        )

    def public(self) -> dict[str, Any]:
        return {
            "id": self.job_id,
            "project": Path(self.project).name,
            "state": self.state,
            "request": self.request[:240],
            "result": self.result[:1000],
            "error": self.error[:500],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "branch": self.branch,
            "base_branch": self.base_branch,
            "changed_files": list(self.changed_files or []),
            "commit_sha": self.commit_sha,
            "pr_number": self.pr_number,
            "pr_url": self.pr_url,
        }


def _parse_codex_reply(stdout: str) -> str:
    messages: list[str] = []
    for raw_line in stdout.splitlines():
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "agent_message":
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            messages.append(text.strip())
    return messages[-1] if messages else ""


class CodexJobManager:
    """Run at most one Codex coding process without blocking voice turns."""

    ACTIVE_STATES = {
        "queued",
        "inspecting",
        "coding",
        "testing",
        "cancelling",
        "publishing",
    }
    TERMINAL_STATES = {
        "done",
        "failed",
        "cancelled",
        "interrupted",
        "published",
        "publish_failed",
        "declined",
    }

    def __init__(
        self,
        settings: Settings,
        events: JobEventSink,
        *,
        clock=time.time,
        publisher: GitHubPublisher | None = None,
    ):
        self.settings = settings
        self.events = events
        self.clock = clock
        self.publisher = publisher or GitHubPublisher(settings)
        self._lock = threading.RLock()
        self._jobs: list[CodexJob] = []
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._notifications: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(
                self.settings.codex_jobs_file.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            payload = []
        if isinstance(payload, dict):
            payload = payload.get("jobs", [])
        if not isinstance(payload, list):
            payload = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                job = CodexJob.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            if job.state == "publishing":
                job.state = "publish_failed"
                job.error = (
                    "BMO 重启前发布状态不明；不会自动续推，"
                    "本地分支已保留，请重新确认后发布"
                )
                job.publish_offer_expires_at = 0
                job.updated_at = self.clock()
            elif job.state in self.ACTIVE_STATES:
                job.state = "interrupted"
                job.error = "BMO 重启前该任务尚未完成"
                job.updated_at = self.clock()
            self._jobs.append(job)
        self._jobs = self._jobs[-self.settings.codex_job_history :]
        if self._jobs:
            self._save_locked()

    def _save_locked(self) -> None:
        path = self.settings.codex_jobs_file
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(
                    {"jobs": [asdict(job) for job in self._jobs]},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        except OSError as exc:
            # Persistence is best-effort: a disk error must not kill the
            # worker thread and strand the job in an active state forever.
            LOG.warning("could not persist codex jobs: %s", exc)

    def _projects(self) -> list[Path]:
        root = self.settings.codex_project_root.expanduser().resolve()
        try:
            children = [
                item.resolve()
                for item in root.iterdir()
                if item.is_dir() and not item.name.startswith(".")
            ]
        except OSError:
            return []
        projects: list[Path] = []
        for item in sorted(children, key=lambda value: value.name.casefold()):
            try:
                item.relative_to(root)
            except ValueError:
                continue
            projects.append(item)
        return projects

    def resolve_project(
        self, request: str, project_hint: str | None = None
    ) -> tuple[Path | None, str]:
        projects = self._projects()
        if not projects:
            return None, "项目目录里还没有可用项目，请先把仓库放进去。"
        lowered = request.casefold()
        hint = (project_hint or "").strip().casefold()
        matched = [
            project
            for project in projects
            if (hint and project.name.casefold() == hint)
            or project.name.casefold() in lowered
        ]
        if len(matched) == 1:
            return matched[0], ""
        if not matched and len(projects) == 1:
            return projects[0], ""
        names = "、".join(project.name for project in projects[:5])
        return None, f"请先告诉我要修改哪个项目。当前可选：{names}。"

    def _active_locked(self) -> CodexJob | None:
        return next(
            (job for job in reversed(self._jobs) if job.state in self.ACTIVE_STATES),
            None,
        )

    def submit(
        self,
        request: str,
        project_hint: str | None = None,
        *,
        request_id: str = "",
        confirmed: bool = False,
    ) -> tuple[CodexJob | None, str]:
        project, problem = self.resolve_project(request, project_hint)
        if project is None:
            return None, problem
        with self._lock:
            active = self._active_locked()
            if active is not None:
                return (
                    None,
                    f"{active.job_id} 仍在执行。你可以询问进度或说取消当前任务。",
                )
            now = self.clock()
            job = CodexJob(
                job_id=f"job-{uuid.uuid4().hex[:8]}",
                request=request,
                project=str(project),
                state="queued",
                created_at=now,
                updated_at=now,
                request_id=request_id,
                confirmed=confirmed,
            )
            self._jobs.append(job)
            self._jobs = self._jobs[-self.settings.codex_job_history :]
            self._save_locked()
            thread = threading.Thread(
                target=self._run,
                args=(job.job_id,),
                name=f"bmo-{job.job_id}",
                daemon=True,
            )
            self._threads[job.job_id] = thread
            thread.start()
        return job, ""

    def _command(self, project: Path) -> list[str]:
        command = [
            self.settings.codex_bin,
            "exec",
            "--json",
            "--sandbox",
            "workspace-write",
            "-c",
            'approval_policy="never"',
            "-C",
            str(project),
        ]
        if self.settings.codex_model:
            command += ["--model", self.settings.codex_model]
        if self.settings.codex_profile:
            command += ["--profile", self.settings.codex_profile]
        command.append("-")
        return command

    @staticmethod
    def _prompt(job: CodexJob) -> str:
        return (
            "<voice_intent>coding</voice_intent>\n"
            f"<job_id>{job.job_id}</job_id>\n"
            f"<request_id>{job.request_id}</request_id>\n"
            f"<project>{Path(job.project).name}</project>\n"
            f"<confirmed>{str(job.confirmed).lower()}</confirmed>\n"
            "<policy>Inspect before editing. Work only in the current project. "
            "Implement the requested change, run relevant tests and static checks, "
            "then return a short spoken summary. Never push, deploy, publish, purchase, "
            "or broadly delete. Do not expose secrets.</policy>\n"
            f"<user_utterance>{job.request}</user_utterance>"
        )

    def _job_locked(self, job_id: str) -> CodexJob | None:
        return next((job for job in self._jobs if job.job_id == job_id), None)

    def _update(
        self,
        job_id: str,
        state: str,
        *,
        result: str = "",
        error: str = "",
        **fields: Any,
    ) -> CodexJob | None:
        with self._lock:
            job = self._job_locked(job_id)
            if job is None:
                return None
            # A pending cancellation must not be clobbered by the worker's
            # lifecycle transitions; only terminal states may follow it.
            if job.state == "cancelling" and state not in self.TERMINAL_STATES:
                return CodexJob.from_dict(asdict(job))
            job.state = state
            job.updated_at = self.clock()
            if result:
                job.result = result
            if error:
                job.error = error
            allowed = {
                "branch",
                "base_branch",
                "base_commit",
                "changed_files",
                "change_digest",
                "commit_sha",
                "pr_number",
                "pr_url",
                "publish_request_id",
                "publish_confirmed_at",
                "publish_offer_expires_at",
            }
            for name, value in fields.items():
                if name not in allowed:
                    raise ValueError(f"unsupported job field: {name}")
                setattr(job, name, value)
            self._save_locked()
            snapshot = CodexJob.from_dict(asdict(job))
        return snapshot

    def _stop_process(self, process: subprocess.Popen[str]) -> None:
        """Terminate the whole process group spawned by ``start_new_session``.

        Codex launches its own child processes; signalling only the direct
        child would leave those running after a cancel or timeout.
        """

        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (OSError, TypeError, AttributeError):
            try:
                process.terminate()
            except OSError:
                return
        try:
            process.wait(timeout=2)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, TypeError, AttributeError):
            try:
                process.kill()
            except OSError:
                return
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            LOG.warning("process %s did not exit after SIGKILL", process.pid)

    def _run(self, job_id: str) -> None:
        job = self._update(job_id, "inspecting")
        if job is None:
            return
        self.events.emit(
            "coding",
            intent="coding",
            job=job.public(),
            subtitle=f"{Path(job.project).name}：正在检查项目",
        )
        process: subprocess.Popen[str] | None = None
        try:
            prepared = self.publisher.prepare(Path(job.project), job.job_id)
            job = self._update(
                job_id,
                "inspecting",
                branch=prepared.branch,
                base_branch=prepared.base_branch,
                base_commit=prepared.base_commit,
            )
            if job is None:
                return
            process = subprocess.Popen(
                self._command(Path(job.project)),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            with self._lock:
                self._processes[job_id] = process
                current = self._job_locked(job_id)
                cancel_pending = (
                    current is not None and current.state == "cancelling"
                )
            if cancel_pending:
                # Cancel arrived before the process was registered; stop it
                # right away instead of running the full job.
                self._stop_process(process)
            job = self._update(job_id, "coding")
            if job is not None and not cancel_pending:
                self.events.emit(
                    "coding",
                    intent="coding",
                    job=job.public(),
                    subtitle=f"{Path(job.project).name}：正在修改和测试",
                )
            stdout, stderr = process.communicate(
                input=self._prompt(job),
                timeout=self.settings.codex_timeout_seconds,
            )
            with self._lock:
                current = self._job_locked(job_id)
                was_cancelled = (
                    cancel_pending
                    or current is not None and current.state == "cancelling"
                )
            if was_cancelled:
                finished = self._update(job_id, "cancelled")
                message = f"{job_id} 已取消。"
                event_state = "cancelled"
            elif process.returncode != 0:
                detail = (stderr.strip() or stdout.strip())[-2000:]
                finished = self._update(
                    job_id, "failed", error=detail or "Codex job failed"
                )
                message = f"{job_id} 执行失败，请查看屏幕上的错误。"
                event_state = "failed"
            else:
                reply = _parse_codex_reply(stdout)
                if not reply:
                    finished = self._update(
                        job_id, "failed", error="Codex returned no agent message"
                    )
                    message = f"{job_id} 没有返回结果。"
                    event_state = "failed"
                else:
                    prepared = self.publisher.collect(
                        PreparedPublish(
                            project=Path(job.project),
                            job_id=job.job_id,
                            branch=job.branch,
                            base_branch=job.base_branch,
                            base_commit=job.base_commit,
                        )
                    )
                    changed_files = list(prepared.changed_paths)
                    if changed_files:
                        finished = self._update(
                            job_id,
                            "ready",
                            result=reply,
                            changed_files=changed_files,
                            change_digest=prepared.change_digest,
                            publish_offer_expires_at=(
                                self.clock()
                                + self.settings.confirmation_timeout_seconds
                            ),
                        )
                        message = (
                            f"{job_id} 已完成，改动 {len(changed_files)} 个文件。"
                            "如需创建 GitHub PR，请说「确认发布」；"
                            "说「不发布」会把改动保留在本地分支。"
                        )
                        event_state = "ready"
                    else:
                        finished = self._update(job_id, "done", result=reply)
                        message = f"{job_id} 已完成，但没有代码改动。{reply[:800]}"
                        event_state = "done"
        except PublishError as exc:
            finished = self._update(job_id, "failed", error=str(exc))
            message = f"{job_id} 未启动 coding：{exc}"
            event_state = "failed"
        except FileNotFoundError:
            finished = self._update(
                job_id,
                "failed",
                error=f"Codex executable not found: {self.settings.codex_bin}",
            )
            message = f"{job_id} 无法启动 Codex。"
            event_state = "failed"
        except subprocess.TimeoutExpired:
            if process is not None:
                self._stop_process(process)
            finished = self._update(job_id, "failed", error="Codex job timed out")
            message = f"{job_id} 超时，已经停止。"
            event_state = "failed"
        except Exception as exc:
            finished = self._update(job_id, "failed", error=str(exc))
            message = f"{job_id} 执行失败。"
            event_state = "failed"
        finally:
            with self._lock:
                self._processes.pop(job_id, None)
                self._threads.pop(job_id, None)
        if finished is not None:
            self.events.emit(
                event_state,
                intent="coding",
                job=finished.public(),
                subtitle=message[:300],
            )
        self._notifications.put(message)

    def publish_candidate(self) -> CodexJob | None:
        with self._lock:
            job = next(
                (
                    item
                    for item in reversed(self._jobs)
                    if item.state in {"ready", "publish_failed", "declined"}
                    and item.changed_files
                ),
                None,
            )
            return CodexJob.from_dict(asdict(job)) if job is not None else None

    def reoffer_publish(self, job_id: str | None = None) -> str:
        with self._lock:
            job = (
                self._job_locked(job_id)
                if job_id
                else next(
                    (
                        item
                        for item in reversed(self._jobs)
                        if item.state
                        in {"ready", "publish_failed", "declined"}
                        and item.changed_files
                    ),
                    None,
                )
            )
            if job is None:
                return "目前没有可创建 PR 的 coding 改动。"
            job.state = "ready"
            job.publish_offer_expires_at = (
                self.clock() + self.settings.confirmation_timeout_seconds
            )
            job.publish_request_id = ""
            job.publish_confirmed_at = 0
            job.updated_at = self.clock()
            self._save_locked()
            snapshot = CodexJob.from_dict(asdict(job))
        self.events.emit(
            "ready",
            intent="coding",
            job=snapshot.public(),
            subtitle=f"{job.job_id}：等待确认创建 PR",
        )
        return (
            f"请确认：为 {job.job_id} 创建 GitHub PR 吗？"
            "请说「确认发布」或「不发布」。"
        )

    def decline_publish(self, job_id: str) -> str:
        with self._lock:
            job = self._job_locked(job_id)
            if job is None or job.state not in {
                "ready",
                "publish_failed",
            }:
                return "这个任务当前没有等待发布。"
            job.state = "declined"
            job.publish_offer_expires_at = 0
            job.publish_request_id = ""
            job.publish_confirmed_at = 0
            job.updated_at = self.clock()
            self._save_locked()
            snapshot = CodexJob.from_dict(asdict(job))
        self.events.emit(
            "declined",
            intent="coding",
            job=snapshot.public(),
            subtitle="未发布；本地分支和改动已保留",
        )
        return f"好的，不发布。改动保留在本地分支 {job.branch}。"

    def confirm_publish(
        self,
        job_id: str,
        *,
        request_id: str,
    ) -> tuple[CodexJob | None, str]:
        if not request_id:
            return None, "发布确认缺少请求编号，已停止。"
        with self._lock:
            job = self._job_locked(job_id)
            if job is None or job.state != "ready":
                return None, "这个任务当前没有等待发布。"
            if self.clock() > job.publish_offer_expires_at:
                job.publish_offer_expires_at = 0
                self._save_locked()
                return (
                    None,
                    "发布确认已超时。请先说「重新发布」，然后再次确认。",
                )
            job.state = "publishing"
            job.publish_request_id = request_id
            job.publish_confirmed_at = self.clock()
            job.publish_offer_expires_at = 0
            job.updated_at = self.clock()
            self._save_locked()
            snapshot = CodexJob.from_dict(asdict(job))
            thread = threading.Thread(
                target=self._publish_run,
                args=(job.job_id, request_id),
                name=f"bmo-publish-{job.job_id}",
                daemon=True,
            )
            self._threads[job.job_id] = thread
            thread.start()
        self.events.emit(
            "publishing",
            intent="coding",
            job=snapshot.public(),
            subtitle=f"{job.job_id}：正在创建 GitHub PR",
        )
        return snapshot, f"正在为 {job.job_id} 创建 GitHub PR。"

    def _publish_run(self, job_id: str, request_id: str) -> None:
        with self._lock:
            current = self._job_locked(job_id)
            if (
                current is None
                or current.state != "publishing"
                or current.publish_request_id != request_id
            ):
                self._threads.pop(job_id, None)
                return
            job = CodexJob.from_dict(asdict(current))
        prepared = PreparedPublish(
            project=Path(job.project),
            job_id=job.job_id,
            branch=job.branch,
            base_branch=job.base_branch,
            base_commit=job.base_commit,
            changed_paths=tuple(job.changed_files or []),
            change_digest=job.change_digest,
        )
        try:
            result = self.publisher.publish(
                prepared,
                title=job.request,
                body=(
                    "Created by BMO after explicit spoken confirmation.\n\n"
                    f"Job: `{job.job_id}`\n\n"
                    f"Result: {job.result[:2000]}"
                ),
            )
        except PublishError as exc:
            with self._lock:
                current = self._job_locked(job_id)
                if current is not None:
                    current.state = "publish_failed"
                    current.error = str(exc)
                    current.publish_offer_expires_at = 0
                    current.updated_at = self.clock()
                    self._save_locked()
                    finished = CodexJob.from_dict(asdict(current))
                else:
                    finished = None
            message = (
                f"{job_id} 发布失败，本地分支和改动已保留。"
                "如需重试，请说「重新发布」。"
            )
            state = "publish_failed"
        else:
            with self._lock:
                current = self._job_locked(job_id)
                if current is not None:
                    current.state = "published"
                    current.commit_sha = result.commit_sha
                    current.pr_number = result.pr_number
                    current.pr_url = result.pr_url
                    current.error = ""
                    current.updated_at = self.clock()
                    self._save_locked()
                    finished = CodexJob.from_dict(asdict(current))
                else:
                    finished = None
            message = f"{job_id} 的 PR 已创建：{result.pr_url}"
            state = "published"
        finally:
            with self._lock:
                self._threads.pop(job_id, None)
        if finished is not None:
            self.events.emit(
                state,
                intent="coding",
                job=finished.public(),
                subtitle=message[:300],
            )
        self._notifications.put(message)

    def latest(self) -> CodexJob | None:
        with self._lock:
            if not self._jobs:
                return None
            return CodexJob.from_dict(asdict(self._jobs[-1]))

    def status_text(self) -> str:
        job = self.latest()
        if job is None:
            return "目前还没有 coding 任务。"
        project = Path(job.project).name
        labels = {
            "queued": "等待开始",
            "inspecting": "正在检查项目",
            "coding": "正在修改和测试",
            "cancelling": "正在取消",
            "cancelled": "已取消",
            "interrupted": "因 BMO 重启而中断",
            "done": "已完成",
            "failed": "执行失败",
            "ready": "改动已就绪，等待确认创建 PR",
            "publishing": "正在创建 GitHub PR",
            "published": "PR 已创建",
            "publish_failed": "PR 发布失败，改动保留在本地",
            "declined": "未发布，改动保留在本地",
        }
        if job.state in {"done", "ready", "published", "declined"}:
            detail = job.result
        elif job.state in {"failed", "publish_failed"}:
            detail = "错误详情已保存在本机任务记录中。"
        else:
            detail = ""
        suffix = f" {detail[:300]}" if detail else ""
        return (
            f"{job.job_id}，项目 {project}，"
            f"{labels.get(job.state, job.state)}。{suffix}"
        ).strip()

    def cancel(self, job_id: str | None = None) -> str:
        with self._lock:
            job = (
                self._job_locked(job_id)
                if job_id
                else self._active_locked()
            )
            if job is None or job.state not in self.ACTIVE_STATES:
                return "目前没有可以取消的 coding 任务。"
            if job.state == "publishing":
                return "GitHub 发布正在进行，不能安全取消。"
            if job.state == "cancelling":
                return f"{job.job_id} 正在取消中。"
            job.state = "cancelling"
            job.updated_at = self.clock()
            self._save_locked()
            process = self._processes.get(job.job_id)
            if process is not None and process.poll() is None:
                self._stop_process(process)
            return f"正在取消 {job.job_id}。"

    def retry_last(self) -> tuple[CodexJob | None, str]:
        job = self.latest()
        if job is None:
            return None, "没有可以重新执行的 coding 任务。"
        if job.state in {"publish_failed", "declined", "ready"}:
            return None, "代码改动已存在；如需创建 PR，请说「重新发布」。"
        if job.state not in {"failed", "cancelled", "interrupted"}:
            return None, f"{job.job_id} 当前不需要重新执行。"
        routed = route_request(job.request)
        if routed.requires_confirmation and not job.confirmed:
            return None, "该任务涉及高风险操作，请重新说出完整指令并确认。"
        return self.submit(
            job.request,
            Path(job.project).name,
            request_id=job.request_id,
            confirmed=job.confirmed,
        )

    def drain_notifications(self) -> list[str]:
        messages: list[str] = []
        while True:
            try:
                messages.append(self._notifications.get_nowait())
            except queue.Empty:
                return messages

    def wait(self, job_id: str, timeout: float = 5) -> None:
        """Wait for one worker in tests and controlled shutdown paths."""

        with self._lock:
            thread = self._threads.get(job_id)
        if thread is not None:
            thread.join(timeout=timeout)
