"""Authenticated HTTP control plane for BMO's typed PC capabilities."""

from __future__ import annotations

import asyncio
import secrets
import shutil
from contextlib import asynccontextmanager
from typing import Annotated, Any, Dict, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from . import __version__
from .capabilities import CapabilityError, CapabilityRegistry
from .codex_runner import CodexRunner
from .config import ConfigurationError, Settings
from .store import JobStore


class CreateJobRequest(BaseModel):
    capability: Optional[str] = Field(default=None, min_length=3, max_length=100)
    arguments: Dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False
    # Compatibility with the initial website-only API.
    action: Optional[Literal["build_website", "continue_project"]] = None
    project_id: Optional[str] = Field(default=None, min_length=3, max_length=64)
    goal: Optional[str] = Field(default=None, min_length=3, max_length=6000)
    client_request_id: Optional[str] = Field(
        default=None, min_length=8, max_length=100
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.prepare()
    store = JobStore(settings.database_path)
    store.initialize()
    runner = CodexRunner(settings, store)
    capabilities = CapabilityRegistry(settings, store, runner)
    security = HTTPBearer(auto_error=False)

    async def dispatcher(stop_dispatcher: asyncio.Event) -> None:
        while not stop_dispatcher.is_set():
            job = await asyncio.to_thread(store.claim_next_job)
            if job is None:
                try:
                    await asyncio.wait_for(stop_dispatcher.wait(), timeout=0.6)
                except asyncio.TimeoutError:
                    continue
            else:
                try:
                    await capabilities.execute(job)
                except Exception as exc:
                    store.update_job(
                        str(job["id"]), state="failed", error=str(exc)[:6000]
                    )
                    store.add_event(
                        str(job["id"]),
                        "failed",
                        "Worker caught an unexpected job failure",
                    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        del _app
        await asyncio.to_thread(store.recover_interrupted_jobs)
        stop_dispatcher = asyncio.Event()
        task = asyncio.create_task(
            dispatcher(stop_dispatcher), name="bmo-job-dispatcher"
        )
        try:
            yield
        finally:
            stop_dispatcher.set()
            await runner.shutdown()
            await task

    app = FastAPI(
        title="BMO PC Worker",
        version=__version__,
        lifespan=lifespan,
        description="A typed capability worker controlled by physical BMO.",
    )

    async def require_auth(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    ) -> None:
        supplied = credentials.credentials if credentials else ""
        if (
            not credentials
            or credentials.scheme.lower() != "bearer"
            or not secrets.compare_digest(supplied, settings.token)
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid BMO pairing token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/health")
    async def health() -> dict[str, object]:
        return {
            "ok": True,
            "service": "bmo-pc-worker",
            "version": __version__,
            "codex_available": bool(shutil.which(settings.codex_bin)),
            "bind_host": settings.bind_host,
        }

    @app.post(
        "/v1/jobs",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_auth)],
    )
    async def create_job(request: CreateJobRequest) -> dict[str, object]:
        capability_id = request.capability
        arguments = dict(request.arguments)
        if capability_id is None:
            action = request.action or "build_website"
            capability_id = (
                "developer.website.continue"
                if action == "continue_project"
                else "developer.website.build"
            )
            arguments = {
                "project_id": request.project_id,
                "goal": request.goal,
            }
        try:
            prepared = capabilities.prepare(
                capability_id, arguments, request.confirmed
            )
            job, created = await asyncio.to_thread(
                store.create_job,
                action=prepared.action,
                project_id=prepared.project_id,
                goal=prepared.goal,
                workspace=prepared.workspace,
                client_request_id=request.client_request_id,
                arguments=prepared.arguments,
            )
        except CapabilityError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"created": created, "job": _public_job(job)}

    @app.get("/v1/capabilities", dependencies=[Depends(require_auth)])
    async def get_capabilities() -> dict[str, object]:
        return {
            "capabilities": capabilities.list(),
            "policy": {
                "raw_shell": False,
                "arbitrary_paths": False,
                "external_write": False,
                "host_admin": False,
            },
        }

    @app.get(
        "/v1/capabilities/{capability_id}",
        dependencies=[Depends(require_auth)],
    )
    async def get_capability(capability_id: str) -> dict[str, object]:
        descriptor = next(
            (
                item
                for item in capabilities.list()
                if item["id"] == capability_id
            ),
            None,
        )
        if descriptor is None:
            raise HTTPException(status_code=404, detail="capability not found")
        return {"capability": descriptor}

    @app.get("/v1/jobs/{job_id}", dependencies=[Depends(require_auth)])
    async def get_job(job_id: str) -> dict[str, object]:
        job = await asyncio.to_thread(store.get_job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="job not found")
        return {"job": _public_job(job)}

    @app.get("/v1/jobs/{job_id}/events", dependencies=[Depends(require_auth)])
    async def get_events(
        job_id: str,
        after: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> dict[str, object]:
        if not await asyncio.to_thread(store.get_job, job_id):
            raise HTTPException(status_code=404, detail="job not found")
        events = await asyncio.to_thread(store.events_after, job_id, after, limit)
        return {
            "events": events,
            "next_after": events[-1]["seq"] if events else after,
        }

    @app.post(
        "/v1/jobs/{job_id}/cancel",
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_auth)],
    )
    async def cancel_job(job_id: str) -> dict[str, object]:
        if not await asyncio.to_thread(store.get_job, job_id):
            raise HTTPException(status_code=404, detail="job not found")
        changed = await runner.cancel(job_id)
        job = await asyncio.to_thread(store.get_job, job_id)
        return {"accepted": changed, "job": _public_job(job) if job else None}

    app.state.settings = settings
    app.state.store = store
    app.state.runner = runner
    app.state.capabilities = capabilities
    return app


def _configuration_error_app(error: Exception) -> FastAPI:
    del error
    app = FastAPI(title="BMO PC Worker (not configured)")

    @app.get("/health", status_code=503)
    async def health() -> dict[str, object]:
        return {"ok": False, "error": "BMO PC Worker is not safely configured"}

    return app


def _public_job(job: dict[str, object]) -> dict[str, object]:
    public = dict(job)
    public.pop("workspace", None)
    if public.get("error"):
        public["error"] = "job failed; inspect the local worker log"
    return public


try:
    app = create_app()
except ConfigurationError as exc:
    app = _configuration_error_app(exc)


def main() -> None:
    import uvicorn

    settings = Settings.from_env()
    settings.prepare()
    uvicorn.run(
        create_app(settings),
        host=settings.bind_host,
        port=settings.port,
        ssl_certfile=(
            str(settings.tls_cert_file) if settings.tls_cert_file is not None else None
        ),
        ssl_keyfile=(
            str(settings.tls_key_file) if settings.tls_key_file is not None else None
        ),
    )


if __name__ == "__main__":
    main()
