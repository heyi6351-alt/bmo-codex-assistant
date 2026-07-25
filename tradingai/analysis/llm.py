"""Multi-provider LLM client (OpenAI-compatible), FREE tiers with failover.

Thin helper around the ``openai`` client. Instead of a single endpoint, it walks
an ordered chain of FREE providers (NVIDIA → Cloudflare → Groq → Cerebras →
OpenRouter — see ``providers.py``) and uses the first one that answers. So a
NVIDIA free-tier 429 no longer means "stand aside" — the call rolls to the next
free provider and the AI keeps trading.

Each provider gets its OWN rate limiter + 429 circuit-breaker (a throttle on one
never blocks the others). With no extra provider keys set, the chain is
NVIDIA-only and behaviour is unchanged. Includes a defensive JSON parser for
model output.
"""

from __future__ import annotations

import collections
import json
import logging
import os
import re
import threading
import time

from config import CONFIG, STORAGE_DIR
from analysis import providers

log = logging.getLogger(__name__)

_clients: dict[str, object] = {}   # one OpenAI client per provider

# ── PER-PROVIDER rate limiter ─────────────────────────────────────────────────
# Sliding 60s window (per provider) shared across worker threads, plus a shared
# cross-process window (one lock-guarded file per provider) so all symbol
# processes together stay under each free tier's ceiling. A 429 trips a
# per-provider circuit breaker with exponential backoff; a success resets it.
_rl_lock = threading.Lock()
_call_times: dict[str, "collections.deque[float]"] = {}
_penalty_until: dict[str, float] = {}
_consec_429: dict[str, int] = {}


def _times(provider: str) -> "collections.deque[float]":
    dq = _call_times.get(provider)
    if dq is None:
        dq = collections.deque()
        _call_times[provider] = dq
    return dq


def _shared_paths(provider: str) -> tuple[str, str]:
    base = str(STORAGE_DIR / f"rpm_{provider}.json")
    return base, base + ".lock"


def _acquire_lock(lock_path: str, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.close(fd)
            return True
        except FileExistsError:
            # Break a stale lock (a crashed holder) after the timeout.
            try:
                if time.monotonic() > deadline:
                    if time.time() - os.path.getmtime(lock_path) > 5.0:
                        os.remove(lock_path)
                        continue
                    return False
            except OSError:
                return False
            time.sleep(0.02)


def _release_lock(lock_path: str) -> None:
    try:
        os.remove(lock_path)
    except OSError:
        pass


def _global_reserve(key: str, rpm: int) -> float:
    """Reserve a slot in `key`'s GLOBAL 60s window (across all symbol processes).
    `key` is the per-target rate-limit key (e.g. "nvidia_0"). Returns 0.0 if
    reserved (proceed), else seconds to wait. Best-effort: on any failure it
    degrades to per-process limiting."""
    rl_path, lock_path = _shared_paths(key)
    try:
        if not _acquire_lock(lock_path, 2.0):
            return 0.0
        try:
            now = time.time()
            times: list = []
            try:
                if os.path.exists(rl_path):
                    times = json.loads(open(rl_path, encoding="utf-8").read()) or []
            except Exception:  # noqa: BLE001
                times = []
            times = [t for t in times if now - t < 60.0]
            gmax = max(1, rpm)
            # SMOOTHING: space calls ~evenly (60/gmax apart) so we NEVER burst.
            min_gap = 60.0 / gmax
            if times and (now - max(times)) < min_gap:
                return min_gap - (now - max(times))
            if len(times) < gmax:
                times.append(now)
                open(rl_path, "w", encoding="utf-8").write(json.dumps(times))
                return 0.0
            return 60.0 - (now - min(times)) + 0.1     # window full — wait for oldest to age out
        finally:
            _release_lock(lock_path)
    except Exception:  # noqa: BLE001 — never block trading on a limiter bug
        return 0.0


def note_rate_limit(key: str, seconds: float = 8.0) -> None:
    """Trip `key`'s circuit breaker after a 429 — mild EXPONENTIAL backoff on repeats,
    CAPPED at 60s (one full per-minute rate window). A free key that 429s just needs
    its 60s window to roll over, NOT a 5-minute parking that starves the confirmer and
    forces slow-fallback timeouts → fail-closed. With the 2 keys now round-robined
    (providers.resolve_chain) real 429s are rare; when one happens the key is back
    within a window. `key` is per-target ("nvidia_0"/"nvidia_1"), so one throttling
    never stops the other. A successful call on that key resets it (see chat())."""
    with _rl_lock:
        n = _consec_429.get(key, 0) + 1
        _consec_429[key] = n
        secs = min(seconds * (2 ** min(n - 1, 4)), 60.0)
        _penalty_until[key] = max(_penalty_until.get(key, 0.0),
                                  time.monotonic() + secs)


def note_success(key: str) -> None:
    """A call went through on `key` → clear its 429 backoff escalation."""
    _consec_429[key] = 0


def rate_limited(rl_key: str | None = None) -> bool:
    """True while inside a 429 cooldown. With no key given, True only when EVERY
    target in the default chain (both NVIDIA keys + all providers) is cooling down
    — so a caller stands aside only when nothing at all is available; otherwise
    failover/rotation can still place the trade."""
    now = time.monotonic()
    if rl_key is not None:
        return now < _penalty_until.get(rl_key, 0.0)
    try:
        chain = providers.resolve_chain(CONFIG.model_quick)
    except Exception:  # noqa: BLE001
        return False
    if not chain:
        return True
    return all(now < _penalty_until.get(t.rl_key, 0.0) for t in chain)


# Max seconds a call may WAIT for one provider's rate-limit slot before giving up and
# moving to the NEXT provider. Without this bound, when the fast providers are in 429
# cooldown, EVERY caller (autonomous, macro, manager, confirms) queued up behind the
# tiny-quota provider (Cerebras 4 RPM) and the whole AI layer serialized into a
# multi-minute pile-up. Skipping ahead keeps latency bounded; the chain has slack.
THROTTLE_MAX_WAIT = 8.0


def _throttle(key: str, rpm: int, max_wait: float = THROTTLE_MAX_WAIT) -> bool:
    """Reserve a rate-limit slot for `key`. Returns True when reserved; False if the
    required wait exceeds `max_wait` (caller should SKIP to the next provider)."""
    waited = 0.0
    while True:
        with _rl_lock:
            now = time.monotonic()
            pen = _penalty_until.get(key, 0.0)
            if now < pen:
                wait = pen - now
            else:
                dq = _times(key)
                while dq and now - dq[0] >= 60.0:
                    dq.popleft()
                # PER-PROCESS SMOOTHING: space this process's own calls apart so
                # it can NEVER burst (a burst trips the per-second limit first).
                rpm = max(1, rpm)
                min_gap = 60.0 / rpm
                if dq and (now - dq[-1]) < min_gap:
                    wait = min_gap - (now - dq[-1])
                elif len(dq) < rpm:
                    gwait = _global_reserve(key, rpm)   # also reserve a global slot
                    if gwait <= 0.0:
                        dq.append(now)
                        return True
                    wait = gwait
                else:
                    wait = 60.0 - (now - dq[0]) + 0.05
        if waited + wait > max_wait:
            return False                      # too busy — let the caller try the next provider
        step = min(max(wait, 0.1), 30.0)
        time.sleep(step)
        waited += step


class LLMUnavailable(RuntimeError):
    pass


def available() -> bool:
    # We can call an LLM if ANY provider in the chain has a key configured.
    return bool(providers.resolve_chain(CONFIG.model_quick))


# Per-REQUEST HTTP timeout. Was 120s — insane for a trade decision: a single slow
# NVIDIA request ate the confirmer's whole 18-45s budget so the failover NEVER reached
# fast Groq (→ "timed out … skipping" → fail-closed → no trade). At ~10s a slow/hung
# provider is abandoned quickly and llm.chat rolls to the next key/provider WITHIN the
# budget (nvidia_0 → nvidia_1 → Groq answers in ~2s). Healthy Kimi/Qwen reply in 2-5s,
# so 10s only cuts genuinely-stalled calls. Override via LLM_HTTP_TIMEOUT.
HTTP_TIMEOUT = float(os.getenv("LLM_HTTP_TIMEOUT", "10") or 10)


def _get_client(target: "providers.Target"):
    # Cache by rl_key, not provider: NVIDIA's rotating keys share provider="nvidia"
    # but need DIFFERENT clients (different api_key).
    c = _clients.get(target.rl_key)
    if c is None:
        from openai import OpenAI
        # We handle our own retries/backoff/failover, so disable the SDK's.
        c = OpenAI(base_url=target.base_url, api_key=target.api_key or "unset",
                   timeout=HTTP_TIMEOUT, max_retries=0)
        _clients[target.rl_key] = c
    return c


_UNAVAILABLE_KEYS = ("not found", "does not exist", "unknown model", "invalid model",
                     "model_not_found", "404", "410", "gone", "end of life", "end-of-life")
_TRANSIENT_KEYS = ("timeout", "timed out", "temporar", "overload", "overloaded",
                   "503", "502", "connection", "reset")


def chat(messages, model=None, temperature=0.6, max_tokens=1200,
         tools=None, tool_choice=None, retries=3):
    """Chat completion with FREE multi-provider failover. `model` is a canonical
    (NVIDIA) id; it is translated per-provider. Returns the OpenAI response
    object from the first provider that answers, else raises the last error."""
    canonical = model or CONFIG.model_quick
    chain = providers.resolve_chain(canonical)
    if not chain:
        raise LLMUnavailable("No LLM provider is configured (set NVIDIA_API_KEY or another).")
    last_err: Exception | None = None
    for target in chain:
        rpm = providers._rpm_for(target.provider)
        if rate_limited(target.rl_key):
            log.info("Skipping %s (429 cooldown) for %s", target.rl_key, canonical)
            continue
        client = _get_client(target)
        for attempt in range(max(1, retries)):
            try:
                kwargs = dict(model=target.model_id, messages=messages,
                              temperature=temperature, top_p=0.95, max_tokens=max_tokens)
                if tools:
                    kwargs["tools"] = tools
                    kwargs["tool_choice"] = tool_choice or "auto"
                if not _throttle(target.rl_key, rpm):
                    log.info("Skipping %s (throttle queue > %.0fs) for %s",
                             target.rl_key, THROTTLE_MAX_WAIT, canonical)
                    break                                   # too busy — next provider
                resp = client.chat.completions.create(**kwargs)
                note_success(target.rl_key)
                return resp
            except Exception as e:  # noqa: BLE001
                last_err = e
                msg = str(e).lower()
                is_429 = "429" in msg or "too many" in msg or "rate limit" in msg
                if is_429:
                    note_rate_limit(target.rl_key, 12.0)    # short cooldown (~12→24→48→60s), not 5 min
                    log.warning("%s rate-limited (429) on %s → next target",
                                target.rl_key, target.model_id)
                    break                                   # move to next target now
                if any(k in msg for k in _UNAVAILABLE_KEYS):
                    log.warning("%s: model %s unavailable (%s) → next target",
                                target.rl_key, target.model_id, e)
                    break
                if any(k in msg for k in _TRANSIENT_KEYS) and attempt < retries - 1:
                    wait = 2 * (attempt + 1)
                    log.warning("%s transient (attempt %d): %s — retrying in %ss",
                                target.rl_key, attempt + 1, e, wait)
                    time.sleep(wait)
                    continue
                log.error("%s error on %s: %s → next target",
                          target.rl_key, target.model_id, e)
                break                                       # unknown error → next target
    raise last_err if last_err else RuntimeError("all LLM providers failed")


def _message_text(resp) -> str:
    """Final text from a completion. Some free reasoning models (gpt-oss, GLM,
    Qwen3-thinking) put the answer in ``reasoning_content``/``reasoning`` and leave
    ``content`` empty — fall back to those so a valid reply is never dropped."""
    msg = resp.choices[0].message
    txt = (getattr(msg, "content", None) or "").strip()
    if not txt:
        txt = (getattr(msg, "reasoning_content", None)
               or getattr(msg, "reasoning", None) or "").strip()
    return txt


def complete(system: str, user: str, model=None, temperature=0.6, max_tokens=1200,
             retries=3) -> str:
    """Convenience: single-turn completion → text. `retries` lets non-critical callers
    (e.g. a background advisor) use a single attempt so a transient outage is a quiet
    skip instead of a retry storm."""
    resp = chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        model=model, temperature=temperature, max_tokens=max_tokens, retries=retries,
    )
    return _message_text(resp)


def list_model_ids() -> list[str]:
    """All model ids currently available on the PRIMARY (NVIDIA) endpoint (for /model all)."""
    if not CONFIG.nvidia_api_key:
        return []
    try:
        target = providers.Target("nvidia", CONFIG.nvidia_base_url, CONFIG.nvidia_api_key,
                                   CONFIG.model_quick, "nvidia_0")
        return sorted(m.id for m in _get_client(target).models.list().data)
    except Exception as e:  # noqa: BLE001
        log.warning("models.list() failed: %s", e)
        return []


def _first_balanced_object(text: str) -> str | None:
    """The first balanced {...} object, ignoring braces inside strings — so an
    appended example object or trailing prose after the answer can't corrupt it."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        start = text.find("{", start + 1)
    return None


def parse_json(text: str) -> dict:
    """Best-effort: extract the first JSON object from model output.

    Prefers a fenced ```json block, else the first BALANCED {...} object (so an
    appended example / trailing prose doesn't merge into one unparseable blob),
    then a lenient repair (strip trailing commas). Returns {} only on true failure.
    """
    if not text:
        return {}
    candidates: list[str] = []
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        candidates.append(fenced.group(1))
    bal = _first_balanced_object(text)
    if bal:
        candidates.append(bal)
    greedy = re.search(r"\{.*\}", text, re.DOTALL)
    if greedy:
        candidates.append(greedy.group(0))
    for cand in candidates:
        for attempt in (cand, re.sub(r",\s*([}\]])", r"\1", cand)):   # raw, then trailing-comma repair
            try:
                obj = json.loads(attempt)
                if isinstance(obj, dict):
                    return obj
            except Exception:  # noqa: BLE001
                continue
    return {}
