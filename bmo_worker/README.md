# BMO PC Worker

This is the permissioned PC-side capability worker for physical BMO. It is
**not** BMO's personality, memory, or planner. Armada on BMO's Orange Pi is the
brain: it discovers PC capabilities, asks for confirmation, submits typed jobs,
watches progress, and can stop them.

The worker never accepts raw shell commands, arbitrary absolute paths, dynamic
plugins, or caller-selected executables.

## Phase 1 capability registry

Ready now:

- `developer.website.build` and `developer.website.continue`: Codex edits one
  worker-owned website project, then Playwright checks desktop and mobile.
- `files.list`, `files.read`, and `files.search`: bounded UTF-8 operations under
  named, allowlisted roots. Secret names, key/certificate files, symlinks, Git
  internals, virtual environments, dependencies, and build trees are denied.
- `system.snapshot`: a privacy-limited OS/tool/disk readiness report.

Discoverable but fail-closed until a separate authenticated adapter is paired:

- `browser.research`
- `email.search` and `email.read`
- `desktop.observe` and `desktop.control`

Email will run as a separate least-privilege OAuth service. The coding process
will never receive mailbox credentials or browser profiles. Sending, deleting,
deploying, purchasing, installing, and host administration are not enabled.

## Safety boundaries

- Bearer-token device pairing; every `/v1` route is authenticated. The token can
  live in a private `BMO_WORKER_TOKEN_FILE` instead of process environment.
- Loopback-only startup unless `BMO_SECURE_GATEWAY=1` is explicitly set.
  Non-loopback startup also requires built-in HTTPS via `BMO_TLS_CERT_FILE` and
  `BMO_TLS_KEY_FILE`; the private key must not be accessible to other users.
- Dedicated worker-owned project root, with database/artifacts outside it.
- Durable SQLite jobs, idempotency checks, sequenced events, timeout and cancel.
- Codex is unavailable until the operator confirms an outer OS boundary with
  `BMO_CODEX_ISOLATED=1`. `workspace-write` is used inside that boundary; the
  worker never uses `--yolo` or the sandbox-bypass option.
- Codex receives an allowlisted environment, not the PC's application secrets.
- Static previews reject symlinks and may contact only their exact ephemeral
  verification origin—not other localhost services or the internet.
- API responses use logical IDs and artifact names, not host filesystem paths.

`pc_agent/` remains separate and is not trusted as a general-PC connector.

## Windows setup

Create a dedicated Windows account, WSL2 instance, or container for the coding
worker and expose only the BMO project directory. Then install and sign into the
Codex CLI inside that boundary:

```powershell
cd bmo_worker
py -m venv .venv
.\.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
py -m playwright install chromium
codex login
```

Create a strong pairing token and a new, empty worker directory:

```powershell
$env:BMO_WORKER_TOKEN_FILE = "D:\BMO\secrets\pairing-token"
$env:BMO_WORKSPACE_ROOT = "D:\BMO\projects"
$env:BMO_WORKER_HOST = "127.0.0.1"
$env:BMO_CODEX_ISOLATED = "1"
py -m bmo_worker.service
```

To let BMO read selected non-project folders, name each root explicitly:

```powershell
$env:BMO_READ_ROOTS = "documents=D:\Users\BMO\Documents;downloads=D:\Users\BMO\Downloads"
```

Keep the default loopback bind. For the Orange Pi connection, place the worker
behind an authenticated TLS gateway or Tailscale and add a Windows Firewall rule
restricted to the Pi. Set `BMO_SECURE_GATEWAY=1` only after that gateway exists.
Never expose port `8210` directly to the public internet.

The worker can also terminate TLS itself. Set `BMO_WORKER_HOST=0.0.0.0`,
`BMO_SECURE_GATEWAY=1`, `BMO_TLS_CERT_FILE` and `BMO_TLS_KEY_FILE`. The
certificate must contain the PC hostname or LAN IP used by Armada. Copy only
the issuing CA certificate—not the private key—to BMO and configure it as
`ARMADA_PC_CA_FILE`.

The same environment contract works on macOS. Use a dedicated worker account
or sandbox, keep the token/key files mode `0600`, and expose port `8210` only to
the Orange Pi with the macOS firewall or a private overlay network.

## API

Every `/v1` request requires:

```text
Authorization: Bearer <BMO_WORKER_TOKEN>
```

Discover the typed tool schemas:

```http
GET /v1/capabilities
```

Create a confirmed website job:

```http
POST /v1/jobs
Content-Type: application/json

{
  "capability": "developer.website.build",
  "arguments": {
    "project_id": "restaurant-site",
    "goal": "Build a responsive restaurant website with menu, story and contact sections."
  },
  "confirmed": true,
  "client_request_id": "bmo-voice-20260726-0001"
}
```

The optional `client_request_id` makes retries idempotent and conflicts if the
same ID is later reused for a different request. Other endpoints:

- `GET /health`
- `GET /v1/capabilities`
- `GET /v1/capabilities/{capability_id}`
- `GET /v1/jobs/{job_id}`
- `GET /v1/jobs/{job_id}/events?after=0`
- `POST /v1/jobs/{job_id}/cancel`

Use `developer.website.continue` to resume the most recent Codex thread for a
project. The old website-only `action/project_id/goal` request shape remains a
compatibility adapter.

Run the local test suite with:

```powershell
py -m pip install -r requirements-dev.txt
py -m unittest discover -s tests -v
```

## Armada integration

The supplied `SbxTheDead/armada-advx` repository was inspected at commit
`926c10a35a363aac8f2b03ef7366cd43860c1ba6`. Its `/v1/bmo` body queue and local
factory remain unchanged. The next Armada commit adds a `/v1/pc/*` proxy and
translates major worker events into face/speech states. See
[`ARMADA_INTEGRATION.md`](ARMADA_INTEGRATION.md).
