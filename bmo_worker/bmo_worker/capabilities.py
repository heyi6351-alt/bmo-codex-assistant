"""Typed PC capabilities exposed to BMO without offering a raw shell."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .codex_runner import CodexRunner
from .config import Settings, safe_project_path, validate_project_id
from .store import JobStore
from . import __version__


DENIED_PARTS = {
    ".codex",
    ".git",
    ".gnupg",
    ".ssh",
    ".venv",
    "build",
    "dist",
    "node_modules",
}
DENIED_NAMES = {
    ".env",
    ".env.local",
    "credentials",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
}
DENIED_SUFFIXES = {".key", ".p12", ".pfx", ".pem"}


class CapabilityError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class PreparedJob:
    action: str
    project_id: str
    goal: str
    workspace: Path
    arguments: Dict[str, Any]


@dataclass(frozen=True)
class Capability:
    id: str
    title: str
    description: str
    risk: str
    requires_confirmation: bool
    state: str = "ready"
    setup: Optional[str] = None
    execution: str = "queued"
    input_schema: Optional[Dict[str, Any]] = None

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "risk": self.risk,
            "effect": self.risk,
            "execution": self.execution,
            "version": "1",
            "requires_confirmation": self.requires_confirmation,
            "state": self.state,
            "setup": self.setup,
            "input_schema": self.input_schema or {
                "type": "object",
                "additionalProperties": False,
            },
        }


class CapabilityRegistry:
    """Validates requests and dispatches a deliberately small tool surface."""

    def __init__(
        self, settings: Settings, store: JobStore, codex_runner: CodexRunner
    ) -> None:
        self.settings = settings
        self.store = store
        self.codex_runner = codex_runner

    def list(self) -> List[Dict[str, Any]]:
        codex_state = (
            "ready"
            if self.codex_runner.codex_available and self.settings.codex_isolated
            else "setup_required"
        )
        codex_setup = (
            None
            if codex_state == "ready"
            else "Install/login to Codex, run the worker in an OS-isolated account "
            "or container, then set BMO_CODEX_ISOLATED=1."
        )
        roots = sorted(self.settings.readable_roots())
        capabilities = [
            Capability(
                "developer.website.build",
                "Build a website",
                "Create or replace a self-contained website in an isolated project.",
                "workspace_write",
                True,
                codex_state,
                codex_setup,
                input_schema=self._developer_schema(),
            ),
            Capability(
                "developer.website.continue",
                "Continue a website",
                "Resume the latest Codex thread for an existing BMO project.",
                "workspace_write",
                True,
                codex_state,
                codex_setup,
                input_schema=self._developer_schema(),
            ),
            Capability(
                "files.list",
                "List files",
                f"List a folder inside an allowlisted root: {', '.join(roots)}.",
                "sensitive_read",
                True,
                execution="inline",
                input_schema=self._file_schema(),
            ),
            Capability(
                "files.read",
                "Read a text file",
                f"Read a bounded text file inside an allowlisted root: {', '.join(roots)}.",
                "sensitive_read",
                True,
                execution="inline",
                input_schema=self._file_schema(),
            ),
            Capability(
                "files.search",
                "Search files",
                "Search filenames and bounded text inside an allowlisted root: "
                + ", ".join(roots)
                + ".",
                "sensitive_read",
                True,
                execution="inline",
                input_schema=self._file_schema(search=True),
            ),
            Capability(
                "system.snapshot",
                "Inspect this PC",
                "Report operating-system, CPU, Python and worker-disk information.",
                "local_read",
                False,
                execution="inline",
            ),
            Capability(
                "browser.research",
                "Research on the web",
                "Use an isolated browser and return sources and a summary.",
                "external_read",
                True,
                "setup_required",
                "Connect the authenticated browser sidecar.",
                execution="external",
            ),
            Capability(
                "email.search",
                "Search email",
                "Search message metadata through a read-only mail connector.",
                "sensitive_read",
                True,
                "setup_required",
                "Connect a Gmail or Outlook read-only OAuth adapter.",
                execution="external",
            ),
            Capability(
                "email.read",
                "Read email",
                "Read one selected message through a read-only mail connector.",
                "sensitive_read",
                True,
                "setup_required",
                "Connect a Gmail or Outlook read-only OAuth adapter.",
                execution="external",
            ),
            Capability(
                "desktop.observe",
                "Observe the desktop",
                "Capture a redacted screenshot for BMO to understand PC state.",
                "sensitive_read",
                True,
                "setup_required",
                "Install and pair the desktop-observation sidecar.",
                execution="external",
            ),
            Capability(
                "desktop.control",
                "Control the desktop",
                "Perform an approved, bounded desktop interaction.",
                "host_control",
                True,
                "disabled",
                "Requires a local PC approval window and action-level policy.",
                execution="external",
            ),
        ]
        return [capability.public() for capability in capabilities]

    def prepare(
        self, capability_id: str, arguments: Mapping[str, Any], confirmed: bool
    ) -> PreparedJob:
        descriptors = {item["id"]: item for item in self.list()}
        descriptor = descriptors.get(capability_id)
        if not descriptor:
            raise CapabilityError("unknown capability")
        if descriptor["state"] != "ready":
            raise CapabilityError(
                descriptor.get("setup") or "capability is not ready",
                status_code=503,
            )
        if descriptor["requires_confirmation"] and not confirmed:
            raise CapabilityError(
                "this capability requires explicit user confirmation",
                status_code=428,
            )

        args = dict(arguments)
        if capability_id.startswith("developer.website."):
            return self._prepare_developer(capability_id, args)
        if capability_id.startswith("files."):
            return self._prepare_files(capability_id, args)
        if capability_id == "system.snapshot":
            if args:
                raise CapabilityError("system.snapshot does not accept arguments")
            return PreparedJob(capability_id, "", "Inspect this PC", Path(), {})
        raise CapabilityError("capability has no executor", status_code=503)

    async def execute(self, job: Mapping[str, Any]) -> None:
        capability_id = str(job["action"])
        if capability_id.startswith("developer.website."):
            await self.codex_runner.run_job(str(job["id"]))
            return

        job_id = str(job["id"])
        if self.store.is_cancel_requested(job_id):
            self.store.update_job(job_id, state="cancelled")
            self.store.add_event(job_id, "cancelled", "BMO stopped the queued job")
            return

        self.store.update_job(job_id, state="working")
        self.store.add_event(job_id, "working", f"Running {capability_id}")
        try:
            output = await asyncio.to_thread(
                self._execute_read_capability, capability_id, job["arguments"]
            )
        except Exception as exc:
            self.store.update_job(job_id, state="failed", error=str(exc)[:6000])
            self.store.add_event(job_id, "failed", f"{capability_id} failed")
            return
        self.store.update_job(
            job_id,
            state="done",
            result=f"{capability_id} completed",
            output_json=json.dumps(output, ensure_ascii=True),
            error=None,
        )
        self.store.add_event(job_id, "done", f"{capability_id} completed")

    def _prepare_developer(
        self, capability_id: str, arguments: Dict[str, Any]
    ) -> PreparedJob:
        allowed = {"project_id", "goal"}
        self._reject_unknown(arguments, allowed)
        project_id = validate_project_id(self._string(arguments, "project_id"))
        goal = self._string(arguments, "goal").strip()
        if len(goal) < 3:
            raise CapabilityError("goal must contain at least 3 characters")
        if len(goal) > self.settings.max_goal_chars:
            raise CapabilityError("goal is too long")
        workspace = safe_project_path(self.settings.workspace_root, project_id)
        return PreparedJob(
            capability_id,
            project_id,
            goal,
            workspace,
            {"project_id": project_id, "goal": goal},
        )

    def _prepare_files(
        self, capability_id: str, arguments: Dict[str, Any]
    ) -> PreparedJob:
        allowed = {"root", "path", "query"}
        self._reject_unknown(arguments, allowed)
        root_name = self._string(arguments, "root")
        relative = str(arguments.get("path", "")).strip()
        self._resolve_read_path(root_name, relative)
        normalized: Dict[str, Any] = {"root": root_name, "path": relative}
        if capability_id == "files.search":
            query = self._string(arguments, "query").strip()
            if not 1 <= len(query) <= 200:
                raise CapabilityError("query must contain 1-200 characters")
            normalized["query"] = query
        elif "query" in arguments:
            raise CapabilityError(f"{capability_id} does not accept query")
        return PreparedJob(
            capability_id,
            "",
            f"{capability_id} in {root_name}",
            Path(),
            normalized,
        )

    def _execute_read_capability(
        self, capability_id: str, arguments: Mapping[str, Any]
    ) -> Dict[str, Any]:
        if capability_id == "system.snapshot":
            usage = shutil.disk_usage(self.settings.workspace_root)
            return {
                "os": platform.system(),
                "os_release": platform.release(),
                "architecture": platform.machine(),
                "logical_cpus": os.cpu_count(),
                "python": platform.python_version(),
                "worker_version": __version__,
                "tools": {
                    "codex": self.codex_runner.codex_available,
                    "git": bool(shutil.which("git")),
                    "playwright": self._playwright_available(),
                },
                "worker_disk": {
                    "total_gib": round(usage.total / (1024**3)),
                    "free_gib": round(usage.free / (1024**3)),
                },
            }

        root_name = str(arguments["root"])
        relative = str(arguments.get("path", ""))
        path = self._resolve_read_path(root_name, relative)
        if capability_id == "files.list":
            if not path.is_dir():
                raise CapabilityError("path is not a directory")
            entries = []
            for item in sorted(path.iterdir(), key=lambda value: value.name.lower())[:200]:
                if self._denied(item) or item.is_symlink():
                    continue
                entries.append(
                    {
                        "name": item.name,
                        "type": "directory" if item.is_dir() else "file",
                        "size_bytes": item.stat().st_size if item.is_file() else None,
                    }
                )
            return {
                "root": root_name,
                "path": relative,
                "entries": entries,
                "truncated": len(entries) == 200,
            }
        if capability_id == "files.read":
            if not path.is_file():
                raise CapabilityError("path is not a file")
            data = path.read_bytes()
            if len(data) > self.settings.max_read_bytes:
                raise CapabilityError("file exceeds the configured read limit")
            if b"\x00" in data:
                raise CapabilityError("binary files are not supported")
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise CapabilityError("file is not valid UTF-8 text") from exc
            return {
                "root": root_name,
                "path": relative,
                "content": content,
                "size_bytes": len(data),
            }
        if capability_id == "files.search":
            if not path.is_dir():
                raise CapabilityError("path is not a directory")
            return self._search_files(
                root_name, relative, path, str(arguments["query"])
            )
        raise CapabilityError("capability has no executor")

    def _search_files(
        self, root_name: str, relative: str, path: Path, query: str
    ) -> Dict[str, Any]:
        needle = query.casefold()
        matches: List[Dict[str, Any]] = []
        root = self.settings.readable_roots()[root_name]
        for current, directories, filenames in os.walk(path, followlinks=False):
            directories[:] = [
                name
                for name in directories
                if not name.startswith(".")
                and not (Path(current) / name).is_symlink()
                and not self._denied(Path(current) / name)
            ]
            for filename in filenames:
                if filename.startswith("."):
                    continue
                candidate = Path(current) / filename
                try:
                    if candidate.is_symlink() or self._denied(candidate):
                        continue
                    resolved = candidate.resolve()
                    if not self._is_within(resolved, root):
                        continue
                    relative_name = resolved.relative_to(root).as_posix()
                    if needle in filename.casefold():
                        matches.append({"path": relative_name, "match": "filename"})
                    elif candidate.stat().st_size <= self.settings.max_read_bytes:
                        data = candidate.read_bytes()
                        if (
                            b"\x00" not in data
                            and needle in data.decode("utf-8").casefold()
                        ):
                            matches.append({"path": relative_name, "match": "content"})
                except (OSError, UnicodeError):
                    continue
                if len(matches) >= self.settings.max_search_results:
                    return {
                        "root": root_name,
                        "path": relative,
                        "query": query,
                        "matches": matches,
                        "truncated": True,
                    }
        return {
            "root": root_name,
            "path": relative,
            "query": query,
            "matches": matches,
            "truncated": False,
        }

    def _resolve_read_path(self, root_name: str, relative: str) -> Path:
        roots = self.settings.readable_roots()
        root = roots.get(root_name)
        if root is None:
            raise CapabilityError("unknown readable root")
        relative_path = Path(relative or ".")
        if relative_path.is_absolute():
            raise CapabilityError("path must be relative to the selected root")
        if ".." in relative_path.parts:
            raise CapabilityError("path must not contain parent traversal")
        current = root
        for part in relative_path.parts:
            current = current / part
            if current.is_symlink():
                raise CapabilityError("symbolic links are not readable")
        if self._denied(relative_path):
            raise CapabilityError("sensitive or generated paths are not readable")
        candidate = (root / relative_path).resolve()
        if not self._is_within(candidate, root):
            raise CapabilityError("path escaped the selected readable root")
        return candidate

    @staticmethod
    def _is_within(candidate: Path, root: Path) -> bool:
        root = root.resolve()
        return candidate == root or root in candidate.parents

    @staticmethod
    def _denied(path: Path) -> bool:
        lowered = [part.lower() for part in path.parts]
        name = path.name.lower()
        return (
            any(part in DENIED_PARTS for part in lowered)
            or any(part.startswith(".") for part in lowered if part not in {".", ".."})
            or name in DENIED_NAMES
            or path.suffix.lower() in DENIED_SUFFIXES
            or name.startswith(".env.")
        )

    @staticmethod
    def _playwright_available() -> bool:
        try:
            import playwright  # noqa: F401
        except ImportError:
            return False
        return True

    @staticmethod
    def _developer_schema() -> Dict[str, Any]:
        return {
            "type": "object",
            "required": ["project_id", "goal"],
            "additionalProperties": False,
            "properties": {
                "project_id": {
                    "type": "string",
                    "pattern": "^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$",
                },
                "goal": {"type": "string", "minLength": 3, "maxLength": 6000},
            },
        }

    @staticmethod
    def _file_schema(search: bool = False) -> Dict[str, Any]:
        properties: Dict[str, Any] = {
            "root": {"type": "string"},
            "path": {"type": "string"},
        }
        required = ["root"]
        if search:
            properties["query"] = {
                "type": "string",
                "minLength": 1,
                "maxLength": 200,
            }
            required.append("query")
        return {
            "type": "object",
            "required": required,
            "additionalProperties": False,
            "properties": properties,
        }

    @staticmethod
    def _string(arguments: Mapping[str, Any], key: str) -> str:
        value = arguments.get(key)
        if not isinstance(value, str):
            raise CapabilityError(f"{key} must be a string")
        return value

    @staticmethod
    def _reject_unknown(arguments: Mapping[str, Any], allowed: Iterable[str]) -> None:
        unknown = set(arguments) - set(allowed)
        if unknown:
            raise CapabilityError(f"unsupported arguments: {sorted(unknown)}")
