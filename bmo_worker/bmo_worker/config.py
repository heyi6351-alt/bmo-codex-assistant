"""Configuration and workspace-boundary validation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Tuple


PROJECT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$")


class ConfigurationError(RuntimeError):
    """Raised when the worker would start with an unsafe configuration."""


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class Settings:
    token: str
    workspace_root: Path
    database_path: Path
    artifacts_root: Path
    codex_bin: str = "codex"
    bind_host: str = "127.0.0.1"
    port: int = 8210
    max_runtime_seconds: int = 1800
    max_goal_chars: int = 6000
    max_repair_attempts: int = 2
    read_roots: Tuple[Tuple[str, Path], ...] = ()
    max_read_bytes: int = 262_144
    max_search_results: int = 50
    codex_isolated: bool = False
    secure_gateway: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        token = os.environ.get("BMO_WORKER_TOKEN", "").strip()
        if len(token) < 24:
            raise ConfigurationError(
                "BMO_WORKER_TOKEN is required and must contain at least 24 characters"
            )

        default_root = Path.home() / "BMO" / "projects"
        workspace_root = Path(
            os.environ.get("BMO_WORKSPACE_ROOT", str(default_root))
        ).expanduser()
        database_path = Path(
            os.environ.get(
                "BMO_WORKER_DATABASE", str(workspace_root.parent / "worker.sqlite3")
            )
        ).expanduser()
        artifacts_root = Path(
            os.environ.get(
                "BMO_WORKER_ARTIFACTS", str(workspace_root.parent / "artifacts")
            )
        ).expanduser()
        read_roots = _read_roots(os.environ.get("BMO_READ_ROOTS", ""))

        return cls(
            token=token,
            workspace_root=workspace_root,
            database_path=database_path,
            artifacts_root=artifacts_root,
            codex_bin=os.environ.get("BMO_CODEX_BIN", "codex"),
            bind_host=os.environ.get("BMO_WORKER_HOST", "127.0.0.1"),
            port=_positive_int("BMO_WORKER_PORT", 8210),
            max_runtime_seconds=_positive_int("BMO_JOB_TIMEOUT_SECONDS", 1800),
            max_goal_chars=_positive_int("BMO_MAX_GOAL_CHARS", 6000),
            max_repair_attempts=_positive_int("BMO_MAX_REPAIR_ATTEMPTS", 2),
            read_roots=read_roots,
            max_read_bytes=_positive_int("BMO_MAX_READ_BYTES", 262_144),
            max_search_results=_positive_int("BMO_MAX_SEARCH_RESULTS", 50),
            codex_isolated=os.environ.get("BMO_CODEX_ISOLATED") == "1",
            secure_gateway=os.environ.get("BMO_SECURE_GATEWAY") == "1",
        )

    def prepare(self) -> None:
        if self.bind_host not in {"127.0.0.1", "::1", "localhost"} and not self.secure_gateway:
            raise ConfigurationError(
                "non-loopback BMO_WORKER_HOST requires BMO_SECURE_GATEWAY=1"
            )

        workspace = self.workspace_root.expanduser()
        if workspace.is_symlink():
            raise ConfigurationError("BMO_WORKSPACE_ROOT must not be a symlink")
        workspace.mkdir(parents=True, exist_ok=True)
        workspace = workspace.resolve()
        home = Path.home().resolve()
        if workspace in {Path(workspace.anchor), home}:
            raise ConfigurationError(
                "BMO_WORKSPACE_ROOT must be a dedicated worker directory"
            )

        marker = workspace / ".bmo-worker-root"
        visible_entries = [item for item in workspace.iterdir() if item != marker]
        if visible_entries and not marker.is_file():
            raise ConfigurationError(
                "existing BMO_WORKSPACE_ROOT is not worker-managed; create "
                "an empty dedicated directory"
            )
        if not marker.exists():
            marker.write_text("BMO PC Worker managed root\n", encoding="utf-8")

        database = self.database_path.expanduser().resolve()
        artifacts = self.artifacts_root.expanduser().resolve()
        if _is_within(database, workspace) or _is_within(artifacts, workspace):
            raise ConfigurationError(
                "database and artifacts must stay outside the Codex workspace"
            )
        if _is_within(database, artifacts):
            raise ConfigurationError("database must stay outside the artifacts tree")
        database.parent.mkdir(parents=True, exist_ok=True)
        artifacts.mkdir(parents=True, exist_ok=True)

    def readable_roots(self) -> Dict[str, Path]:
        roots = {"projects": self.workspace_root.resolve()}
        for name, path in self.read_roots:
            roots[name] = path.resolve()
        return roots


def _read_roots(raw: str) -> Tuple[Tuple[str, Path], ...]:
    """Parse name=/path entries separated by the platform path separator."""

    roots = []
    seen = {"projects"}
    for entry in raw.split(os.pathsep):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise ConfigurationError(
                "BMO_READ_ROOTS entries must use name=/absolute/path"
            )
        name, raw_path = (part.strip() for part in entry.split("=", 1))
        if not re.fullmatch(r"[a-z][a-z0-9_-]{1,31}", name):
            raise ConfigurationError(
                "BMO_READ_ROOTS names must be 2-32 lowercase letters, digits, "
                "hyphens or underscores"
            )
        if name in seen:
            raise ConfigurationError(f"duplicate BMO_READ_ROOTS name: {name}")
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            raise ConfigurationError("BMO_READ_ROOTS paths must be absolute")
        roots.append((name, path))
        seen.add(name)
    return tuple(roots)


def validate_project_id(project_id: str) -> str:
    project_id = project_id.strip().lower()
    if not PROJECT_ID_RE.fullmatch(project_id):
        raise ValueError(
            "project_id must be 3-64 lowercase letters, digits, hyphens or underscores"
        )
    return project_id


def safe_project_path(workspace_root: Path, project_id: str) -> Path:
    """Resolve a server-issued project ID without accepting a caller path."""

    clean_id = validate_project_id(project_id)
    root = workspace_root.resolve()
    candidate = (root / clean_id).resolve()
    if candidate.parent != root:
        raise ValueError("project escaped the configured workspace root")
    raw_candidate = root / clean_id
    if raw_candidate.is_symlink():
        raise ValueError("project workspace must not be a symlink")
    return candidate


def _is_within(candidate: Path, root: Path) -> bool:
    root = root.resolve()
    return candidate == root or root in candidate.parents
