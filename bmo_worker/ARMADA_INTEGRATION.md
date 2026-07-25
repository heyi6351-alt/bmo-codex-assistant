# Armada → PC integration

This contract keeps Armada on BMO's Orange Pi as the brain and treats the PC as
a typed capability provider.

The reference is `SbxTheDead/armada-advx` main commit
`926c10a35a363aac8f2b03ef7366cd43860c1ba6`.

## Preserve current Armada routes

Do not change the payloads or behavior of:

- `POST /v1/bmo`
- `GET /v1/bmo/next`
- `/v1/factory*`

Add an optional Armada loopback proxy:

- `GET /v1/pc/capabilities`
- `POST /v1/pc/jobs`
- `GET /v1/pc/jobs/{id}`
- `GET /v1/pc/jobs/{id}/events?after=N`
- `POST /v1/pc/jobs/{id}/cancel`

Armada calls the worker's matching `/v1/*` routes over authenticated HTTPS on a
Tailnet/private link. Suggested configuration:

```text
ARMADA_PC_URL=https://bmo-pc.example/v1
ARMADA_PC_TOKEN_FILE=~/.armada/pc-token
ARMADA_PC_CA_FILE=~/.armada/pc-ca.pem
```

If these are unset, Armada continues working and `/v1/pc/*` returns `503`.
Tokens must never appear in task output, BMO speech, or the JSON state file.

## Job request

```json
{
  "capability": "developer.website.build",
  "arguments": {
    "project_id": "portfolio",
    "goal": "Build and verify a responsive portfolio website"
  },
  "confirmed": true,
  "client_request_id": "bmo-device-voice-000001"
}
```

`client_request_id` is required for Armada mutations and must be unique per BMO
device. Retrying the same request is safe; reusing it with different arguments
returns a conflict.

Armada must discover `/v1/capabilities` instead of assuming a capability exists.
`setup_required`, `disabled`, and `unavailable` are not invokable.

## BMO body behavior

Translate only major event transitions into the existing body queue:

| Worker event/state | Existing BMO command |
| --- | --- |
| queued/running/coding | `face thinking` |
| verifying | `face focused` |
| done | `face happy`, then a short `say` summary |
| failed | `face sad`, then a safe `say` summary |
| cancelled | `face neutral`, `say canceled` |

Do not enqueue every Codex log line; Armada's current queue holds only 16
commands and destructively pops them.

## Capability isolation

Each sensitive family becomes a separate adapter:

- Coding: OS-isolated account/container, project workspace only.
- Email: Gmail/Outlook read-only OAuth, token in the PC credential vault.
- Browser: clean browser profile, no personal signed-in session by default.
- Desktop: local observation/control sidecar with a visible PC approval window.

Email bodies and websites are untrusted data, never instructions. BMO must not
open message links or attachments automatically. Sending email, deleting data,
deploying, purchases, installations, and administrative changes require a fresh
PC-side approval and are outside Phase 1.
