"""Safe, explicit GitHub pull-request publication for completed coding jobs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from .config import Settings

_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_PR_URL = re.compile(r"^https://github\.com/[^/\s]+/[^/\s]+/pull/(\d+)/?$")


class PublishError(RuntimeError):
    """A safe publication precondition or command failed.

    Callers must leave the local branch and worktree intact when this is raised.
    """


@dataclass(frozen=True)
class PreparedPublish:
    project: Path
    job_id: str
    branch: str
    base_branch: str
    base_commit: str
    changed_paths: tuple[str, ...] = ()
    change_digest: str = ""


@dataclass(frozen=True)
class PublishResult:
    commit_sha: str
    pr_number: int
    pr_url: str


Runner = Callable[[list[str], Path], subprocess.CompletedProcess[str]]


class GitHubPublisher:
    """Prepare a local job branch and publish it only when explicitly called."""

    def __init__(
        self,
        settings: Settings,
        *,
        runner: Runner | None = None,
    ):
        self.settings = settings
        if runner is None:
            timeout = settings.publish_timeout_seconds

            def default_runner(
                argv: list[str], cwd: Path
            ) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    argv,
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    check=False,
                    shell=False,
                    timeout=timeout,
                )

            self._runner = default_runner
        else:
            self._runner = runner

    def _project(self, value: str | Path) -> Path:
        root = self.settings.codex_project_root.expanduser().resolve()
        project = Path(value).expanduser().resolve()
        try:
            relative = project.relative_to(root)
        except ValueError as exc:
            raise PublishError("项目不在 BMO 允许的项目目录内") from exc
        if not relative.parts or not project.is_dir():
            raise PublishError("项目目录无效")
        return project

    def _run(
        self,
        argv: list[str],
        cwd: Path,
        failure: str,
        *,
        allowed_returncodes: tuple[int, ...] = (0,),
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = self._runner(argv, cwd)
        except (OSError, subprocess.SubprocessError) as exc:
            raise PublishError(failure) from exc
        if result.returncode not in allowed_returncodes:
            raise PublishError(failure)
        return result

    def _git(
        self,
        project: Path,
        *arguments: str,
        failure: str,
        allowed_returncodes: tuple[int, ...] = (0,),
    ) -> subprocess.CompletedProcess[str]:
        return self._run(
            [self.settings.git_bin, *arguments],
            project,
            failure,
            allowed_returncodes=allowed_returncodes,
        )

    def _gh(
        self,
        project: Path,
        *arguments: str,
        failure: str,
        allowed_returncodes: tuple[int, ...] = (0,),
    ) -> subprocess.CompletedProcess[str]:
        return self._run(
            [self.settings.gh_bin, *arguments],
            project,
            failure,
            allowed_returncodes=allowed_returncodes,
        )

    def prepare(self, project: str | Path, job_id: str) -> PreparedPublish:
        """Require a clean repository and create a local ``bmo/<job>`` branch.

        This method performs no network operation and never commits.
        """

        project_path = self._project(project)
        if not _SAFE_JOB_ID.fullmatch(job_id) or ".." in job_id:
            raise PublishError("任务号不能安全地用于 Git 分支")
        branch = f"bmo/{job_id}"

        top = self._git(
            project_path,
            "rev-parse",
            "--show-toplevel",
            failure="项目不是有效的 Git 仓库",
        ).stdout.strip()
        try:
            if Path(top).resolve() != project_path:
                raise PublishError("项目必须是 Git 仓库根目录")
        except OSError as exc:
            raise PublishError("无法确认 Git 仓库根目录") from exc

        status = self._git(
            project_path,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            failure="无法检查 Git 工作区",
        ).stdout
        if status:
            raise PublishError("Git 工作区已有未提交改动，未启动 coding")

        base_branch = self._git(
            project_path,
            "symbolic-ref",
            "--quiet",
            "--short",
            "HEAD",
            failure="项目处于 detached HEAD，无法创建安全分支",
        ).stdout.strip()
        base_commit = self._git(
            project_path,
            "rev-parse",
            "HEAD",
            failure="项目还没有可用的基础提交",
        ).stdout.strip()
        if not base_branch or not base_commit:
            raise PublishError("无法确定 PR 的基础分支和提交")
        if base_branch.startswith("bmo/"):
            raise PublishError("当前仍在旧的 BMO 分支，请先处理已有任务")

        self._git(
            project_path,
            "checkout",
            "-b",
            branch,
            failure="无法创建 BMO coding 分支",
        )
        return PreparedPublish(
            project=project_path,
            job_id=job_id,
            branch=branch,
            base_branch=base_branch,
            base_commit=base_commit,
        )

    @staticmethod
    def _status_paths(output: str) -> tuple[str, ...]:
        tokens = output.split("\0")
        paths: list[str] = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            index += 1
            if not token:
                continue
            if len(token) < 4:
                raise PublishError("Git 状态输出无法安全解析")
            status = token[:2]
            path = token[3:]
            if not path or Path(path).is_absolute() or ".." in Path(path).parts:
                raise PublishError("Git 改动路径越过项目边界")
            paths.append(path)
            if ("R" in status or "C" in status) and index < len(tokens):
                original = tokens[index]
                index += 1
                if (
                    not original
                    or Path(original).is_absolute()
                    or ".." in Path(original).parts
                ):
                    raise PublishError("Git 重命名路径越过项目边界")
                paths.append(original)
        return tuple(dict.fromkeys(paths))

    def collect(self, prepared: PreparedPublish) -> PreparedPublish:
        project = self._project(prepared.project)
        output = self._git(
            project,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            failure="无法读取 coding 改动",
        ).stdout
        paths = self._status_paths(output)
        digest = self._change_digest(project, paths) if paths else ""
        return replace(
            prepared,
            project=project,
            changed_paths=paths,
            change_digest=digest,
        )

    @staticmethod
    def _change_digest(project: Path, paths: tuple[str, ...]) -> str:
        """Bind a confirmation to exact contents, not only changed names."""

        digest = hashlib.sha256()
        for relative in sorted(paths):
            encoded = relative.encode("utf-8", errors="surrogateescape")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
            path = project / relative
            try:
                stat = path.lstat()
            except FileNotFoundError:
                digest.update(b"\0deleted")
                continue
            digest.update(b"\0mode:")
            digest.update(str(stat.st_mode).encode("ascii"))
            if path.is_symlink():
                target = os.readlink(path).encode(
                    "utf-8", errors="surrogateescape"
                )
                digest.update(b"\0symlink:")
                digest.update(target)
                continue
            if not path.is_file():
                digest.update(b"\0non-file")
                continue
            digest.update(b"\0file:")
            try:
                with path.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        digest.update(chunk)
            except OSError as exc:
                raise PublishError("无法锁定 coding 改动内容") from exc
        return digest.hexdigest()

    def _existing_pr(
        self, project: Path, branch: str
    ) -> tuple[int, str] | None:
        result = self._gh(
            project,
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "open",
            "--limit",
            "1",
            "--json",
            "number,url",
            failure="无法检查已有 Pull Request",
        )
        try:
            payload = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise PublishError("GitHub CLI 返回了无效结果") from exc
        if not isinstance(payload, list) or not payload:
            return None
        item = payload[0]
        if not isinstance(item, dict):
            raise PublishError("GitHub CLI 返回了无效 PR 记录")
        number = item.get("number")
        url = item.get("url")
        if not isinstance(number, int) or not isinstance(url, str):
            raise PublishError("GitHub CLI 返回的 PR 信息不完整")
        if _PR_URL.fullmatch(url) is None:
            raise PublishError("GitHub CLI 返回了无效 PR 地址")
        return number, url

    def _restore_base_branch(self, prepared: PreparedPublish) -> None:
        """Best-effort cleanup after a PR exists.

        A failed checkout does not invalidate an already-created PR. Leaving
        the BMO branch checked out is safe because ``prepare`` rejects chained
        jobs from BMO branches.
        """

        try:
            self._git(
                prepared.project,
                "checkout",
                prepared.base_branch,
                failure="PR 已创建，但无法切回基础分支",
            )
        except PublishError:
            pass

    def publish(
        self,
        prepared: PreparedPublish,
        *,
        title: str,
        body: str,
    ) -> PublishResult:
        """Commit, push the job branch, and create a PR.

        This is the only method that performs external writes. Callers must gate
        it behind a fresh user confirmation bound to the same job.
        """

        project = self._project(prepared.project)
        if not prepared.branch.startswith("bmo/"):
            raise PublishError("拒绝发布非 BMO 分支")
        if prepared.base_branch.startswith("bmo/"):
            raise PublishError("拒绝把 PR 建立在另一个 BMO 任务分支上")
        if prepared.branch in {
            "main",
            "master",
            prepared.base_branch,
        }:
            raise PublishError("拒绝直接发布基础分支")

        current = self._git(
            project,
            "branch",
            "--show-current",
            failure="无法确认当前 Git 分支",
        ).stdout.strip()
        if current != prepared.branch:
            raise PublishError("项目已离开该任务的 BMO 分支")

        self._git(
            project,
            "merge-base",
            "--is-ancestor",
            prepared.base_commit,
            "HEAD",
            failure="任务分支不再基于原始提交",
        )

        current_status = self.collect(prepared)
        head = self._git(
            project,
            "rev-parse",
            "HEAD",
            failure="无法读取当前提交",
        ).stdout.strip()

        if head == prepared.base_commit:
            if not prepared.changed_paths:
                raise PublishError("任务没有可发布的代码改动")
            if set(current_status.changed_paths) != set(prepared.changed_paths):
                raise PublishError("确认后检测到额外改动，已停止发布")
            if (
                not prepared.change_digest
                or current_status.change_digest != prepared.change_digest
            ):
                raise PublishError("确认后检测到改动内容变化，已停止发布")
            self._gh(
                project,
                "auth",
                "status",
                failure="GitHub CLI 尚未登录",
            )
            self._git(
                project,
                "add",
                "-A",
                "--",
                *prepared.changed_paths,
                failure="无法暂存任务改动",
            )
            message = f"BMO {prepared.job_id}: {title.strip()[:120]}"
            self._git(
                project,
                "-c",
                f"user.name={self.settings.git_author_name}",
                "-c",
                f"user.email={self.settings.git_author_email}",
                "commit",
                "-m",
                message,
                failure="无法提交任务改动",
            )
            head = self._git(
                project,
                "rev-parse",
                "HEAD",
                failure="无法读取发布提交",
            ).stdout.strip()
        else:
            if current_status.changed_paths:
                raise PublishError("发布重试前发现未提交改动，已停止")
            committed_paths = self._git(
                project,
                "diff",
                "--name-only",
                "-z",
                f"{prepared.base_commit}..HEAD",
                failure="无法核对已提交改动",
            ).stdout
            paths = tuple(path for path in committed_paths.split("\0") if path)
            if set(paths) != set(prepared.changed_paths):
                raise PublishError("已有提交与原任务改动不一致")
            self._gh(
                project,
                "auth",
                "status",
                failure="GitHub CLI 尚未登录",
            )

        self._git(
            project,
            "push",
            "--set-upstream",
            "origin",
            f"refs/heads/{prepared.branch}:refs/heads/{prepared.branch}",
            failure="无法推送 BMO 任务分支",
        )

        existing = self._existing_pr(project, prepared.branch)
        if existing is not None:
            number, url = existing
            self._restore_base_branch(prepared)
            return PublishResult(head, number, url)

        result = self._gh(
            project,
            "pr",
            "create",
            "--base",
            prepared.base_branch,
            "--head",
            prepared.branch,
            "--title",
            title.strip()[:120] or f"BMO {prepared.job_id}",
            "--body",
            body,
            failure="任务分支已保留，但创建 Pull Request 失败",
        )
        url = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        match = _PR_URL.fullmatch(url)
        if match is None:
            raise PublishError("任务分支已保留，但 GitHub 未返回有效 PR 地址")
        self._restore_base_branch(prepared)
        return PublishResult(head, int(match.group(1)), url)
