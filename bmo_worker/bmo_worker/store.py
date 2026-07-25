"""Small durable SQLite job and event store."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TERMINAL_STATES = {"done", "failed", "cancelled"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.database_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    client_request_id TEXT UNIQUE,
                    action TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    state TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    arguments_json TEXT NOT NULL DEFAULT '{}',
                    thread_id TEXT,
                    result TEXT,
                    output_json TEXT,
                    error TEXT,
                    verification_json TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS jobs_state_created_idx
                    ON jobs(state, created_at);
                CREATE INDEX IF NOT EXISTS jobs_project_created_idx
                    ON jobs(project_id, created_at);

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    seq INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    message TEXT NOT NULL,
                    data_json TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(job_id, seq)
                );
                """
            )
            columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "arguments_json" not in columns:
                db.execute(
                    "ALTER TABLE jobs ADD COLUMN arguments_json TEXT NOT NULL DEFAULT '{}'"
                )
            if "output_json" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN output_json TEXT")

    @staticmethod
    def _job(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["cancel_requested"] = bool(item["cancel_requested"])
        raw_arguments = item.pop("arguments_json", None)
        item["arguments"] = json.loads(raw_arguments) if raw_arguments else {}
        raw_output = item.pop("output_json", None)
        item["output"] = json.loads(raw_output) if raw_output else None
        raw = item.pop("verification_json", None)
        item["verification"] = json.loads(raw) if raw else None
        return item

    def create_job(
        self,
        *,
        action: str,
        project_id: str,
        goal: str,
        workspace: Path,
        client_request_id: str | None,
        arguments: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        now = utc_now()
        with self._connect() as db:
            if client_request_id:
                row = db.execute(
                    "SELECT * FROM jobs WHERE client_request_id = ?",
                    (client_request_id,),
                ).fetchone()
                if row:
                    job = self._job(row)
                    assert job is not None
                    expected = arguments or {}
                    if (
                        job["action"] != action
                        or job["project_id"] != project_id
                        or job["goal"] != goal
                        or job["arguments"] != expected
                    ):
                        raise ValueError(
                            "client_request_id was already used for a different job"
                        )
                    return job, False

            job_id = str(uuid.uuid4())
            db.execute(
                """
                INSERT INTO jobs (
                    id, client_request_id, action, project_id, goal, state,
                    workspace, arguments_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)
                """,
                (
                    job_id,
                    client_request_id,
                    action,
                    project_id,
                    goal,
                    str(workspace),
                    json.dumps(arguments or {}, ensure_ascii=True),
                    now,
                    now,
                ),
            )
        self.add_event(job_id, "queued", f"BMO queued {action}")
        job = self.get_job(job_id)
        assert job is not None
        return job, True

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            return self._job(
                db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            )

    def latest_thread_for_project(self, project_id: str) -> str | None:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT thread_id FROM jobs
                WHERE project_id = ? AND thread_id IS NOT NULL
                ORDER BY created_at DESC LIMIT 1
                """,
                (project_id,),
            ).fetchone()
            return str(row["thread_id"]) if row else None

    def claim_next_job(self) -> dict[str, Any] | None:
        now = utc_now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT * FROM jobs
                WHERE state = 'queued' AND cancel_requested = 0
                ORDER BY created_at LIMIT 1
                """
            ).fetchone()
            if row is None:
                db.commit()
                return None
            db.execute(
                "UPDATE jobs SET state = 'running', updated_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            db.commit()
        self.add_event(str(row["id"]), "running", "PC Worker started the job")
        return self.get_job(str(row["id"]))

    def update_job(self, job_id: str, **fields: Any) -> None:
        allowed = {
            "state",
            "thread_id",
            "result",
            "error",
            "verification_json",
            "output_json",
            "cancel_requested",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"unsupported job fields: {sorted(unknown)}")
        if not fields:
            return
        fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = list(fields.values()) + [job_id]
        with self._connect() as db:
            db.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", values)

    def request_cancel(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if not job or job["state"] in TERMINAL_STATES:
            return False
        self.update_job(job_id, cancel_requested=1)
        self.add_event(job_id, "cancel_requested", "BMO requested cancellation")
        if job["state"] == "queued":
            self.update_job(job_id, state="cancelled")
            self.add_event(job_id, "cancelled", "Queued job was cancelled")
        return True

    def is_cancel_requested(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        return bool(job and job["cancel_requested"])

    def add_event(
        self,
        job_id: str,
        kind: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> int:
        message = message.strip()[:1200]
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            seq = int(
                db.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM events WHERE job_id = ?",
                    (job_id,),
                ).fetchone()[0]
            )
            db.execute(
                """
                INSERT INTO events(job_id, seq, kind, message, data_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    seq,
                    kind[:80],
                    message,
                    json.dumps(data, ensure_ascii=True) if data else None,
                    utc_now(),
                ),
            )
            db.commit()
        return seq

    def events_after(
        self, job_id: str, after: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT seq, kind, message, data_json, created_at
                FROM events
                WHERE job_id = ? AND seq > ?
                ORDER BY seq LIMIT ?
                """,
                (job_id, after, min(max(limit, 1), 500)),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            raw = item.pop("data_json", None)
            item["data"] = json.loads(raw) if raw else None
            result.append(item)
        return result

    def recover_interrupted_jobs(self) -> int:
        """Fail jobs whose worker disappeared; a user can explicitly continue."""

        with self._connect() as db:
            rows = db.execute(
                """
                SELECT id FROM jobs
                WHERE state NOT IN ('queued', 'done', 'failed', 'cancelled')
                """
            ).fetchall()
            db.execute(
                """
                UPDATE jobs
                SET state = CASE
                        WHEN cancel_requested = 1 THEN 'cancelled'
                        ELSE 'failed'
                    END,
                    error = CASE
                        WHEN cancel_requested = 1 THEN error
                        ELSE 'worker restarted during execution'
                    END,
                    updated_at = ?
                WHERE state NOT IN ('queued', 'done', 'failed', 'cancelled')
                """,
                (utc_now(),),
            )
        for row in rows:
            job = self.get_job(str(row["id"]))
            state = job["state"] if job else "unknown"
            self.add_event(
                str(row["id"]),
                "recovered",
                f"Worker restarted; previous process was reconciled as {state}",
            )
        return len(rows)
