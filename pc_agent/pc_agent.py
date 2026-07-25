"""
BMO PC Agent — "BMO controls the PC".

A small HTTP service that lets BMO's Armada brain drive a real Chromium browser
on THIS Windows PC to look things up (read-only) and report back, like the
`browser-use` library.

browser-use does not install on Python 3.14, so this is the minimal fallback:
Playwright (real Chromium) + the Anthropic/Claude SDK running an agentic
tool-use loop. Claude is given a small set of browser tools (search, navigate,
read, click, finish) and drives the browser a few steps to answer the goal.

Endpoints (0.0.0.0:8200):
  POST /task  {"goal": "..."}  -> {"result": "...", "steps": [...]}
  GET  /health                 -> {"ok": true, ...}

SAFETY: read-only browsing only. The agent never logs in, buys anything, or
submits sensitive forms. Only navigation, reading, and clicking links/buttons
that lead to public information are exposed as tools.
"""

import os
import re
import json
import threading
import traceback
from urllib.parse import quote_plus

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Model is env-overridable. Default to a strong tool-use model; set
# PC_AGENT_MODEL=claude-haiku-4-5 for a cheaper/faster loop if desired.
MODEL = os.environ.get("PC_AGENT_MODEL", "claude-opus-4-8")
MAX_STEPS = int(os.environ.get("PC_AGENT_MAX_STEPS", "12"))
NAV_TIMEOUT_MS = int(os.environ.get("PC_AGENT_NAV_TIMEOUT_MS", "20000"))
HEADLESS = os.environ.get("PC_AGENT_HEADLESS", "1") != "0"
PAGE_TEXT_LIMIT = 6000

SYSTEM_PROMPT = (
    "You are BMO's browser operator. You control a real Chromium browser on the "
    "user's PC to look up information on the public web and report a concise, "
    "accurate answer.\n\n"
    "STRICT SAFETY RULES (read-only browsing only):\n"
    "- Never log into any account, enter passwords, or submit credentials.\n"
    "- Never buy, order, book, pay, or submit any form with personal/financial data.\n"
    "- Only navigate, read page text, and click links/buttons that lead to public info.\n"
    "- If a goal would require any of the forbidden actions, refuse via `finish` and "
    "explain why.\n\n"
    "HOW TO WORK:\n"
    "- Use `web_search` to find sources, then `open_url` and `read_page` to gather facts.\n"
    "- Prefer reputable sources. Keep it to a few steps.\n"
    "- When you have the answer, call `finish` with a short, direct result. Cite the "
    "source URL you used when relevant.\n"
    "- Do not narrate excessively. Be efficient with steps."
)

TOOLS = [
    {
        "name": "web_search",
        "description": "Search the web via DuckDuckGo and return the top results "
        "(titles, snippets, and URLs). Use this first to find sources.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."}
            },
            "required": ["query"],
        },
    },
    {
        "name": "open_url",
        "description": "Navigate the browser to a URL and return the page title and "
        "visible text (truncated). Use for pages found via web_search.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Absolute http(s) URL."}
            },
            "required": ["url"],
        },
    },
    {
        "name": "read_page",
        "description": "Return the current page's title and visible text (truncated). "
        "Use after clicking or if you need to re-read the current page.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "find_links",
        "description": "List clickable link texts and URLs on the current page "
        "(useful to decide what to click next).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "click",
        "description": "Click the first visible link or button whose text contains the "
        "given text (case-insensitive), then return the resulting page text.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to match on a link/button."}
            },
            "required": ["text"],
        },
    },
    {
        "name": "finish",
        "description": "Finish the task and report the final answer to BMO. Call this "
        "exactly once when done (or to refuse an unsafe request).",
        "input_schema": {
            "type": "object",
            "properties": {
                "answer": {"type": "string", "description": "The concise final answer."}
            },
            "required": ["answer"],
        },
    },
]


# ---------------------------------------------------------------------------
# Browser tool implementations (Playwright sync API, run in a worker thread)
# ---------------------------------------------------------------------------


def _clean_text(text: str, limit: int = PAGE_TEXT_LIMIT) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
    if len(text) > limit:
        text = text[:limit] + "\n...[truncated]..."
    return text


class BrowserAgent:
    """Runs one goal to completion in a fresh Chromium context."""

    def __init__(self, page):
        self.page = page

    def _page_dump(self) -> str:
        try:
            title = self.page.title()
        except Exception:
            title = ""
        try:
            body = self.page.inner_text("body")
        except Exception:
            body = ""
        return f"URL: {self.page.url}\nTITLE: {title}\n\n{_clean_text(body)}"

    def web_search(self, query: str) -> str:
        url = "https://duckduckgo.com/html/?q=" + quote_plus(query)
        self.page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        results = []
        for res in self.page.query_selector_all("div.result")[:8]:
            a = res.query_selector("a.result__a")
            if not a:
                continue
            title = (a.inner_text() or "").strip()
            href = a.get_attribute("href") or ""
            snip_el = res.query_selector(".result__snippet")
            snippet = (snip_el.inner_text() or "").strip() if snip_el else ""
            results.append(f"- {title}\n  {href}\n  {snippet}")
        if not results:
            return "No results parsed. Page text:\n" + self._page_dump()
        return "Search results for '%s':\n\n%s" % (query, "\n".join(results))

    def open_url(self, url: str) -> str:
        if not re.match(r"^https?://", url):
            return "Refused: only absolute http(s) URLs are allowed."
        self.page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        return self._page_dump()

    def read_page(self) -> str:
        return self._page_dump()

    def find_links(self) -> str:
        links = []
        for a in self.page.query_selector_all("a[href]")[:60]:
            txt = (a.inner_text() or "").strip()
            href = a.get_attribute("href") or ""
            if txt and href.startswith("http"):
                links.append(f"- {txt} -> {href}")
        return "\n".join(links[:40]) or "No usable links found."

    def click(self, text: str) -> str:
        try:
            el = self.page.get_by_text(text, exact=False).first
            el.click(timeout=NAV_TIMEOUT_MS)
            self.page.wait_for_load_state("domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except Exception as exc:
            return f"Could not click '{text}': {exc}"
        return self._page_dump()


def _run_agent_sync(goal: str) -> dict:
    """Blocking: drives Claude + Playwright to completion. Runs in a thread."""
    import anthropic
    from playwright.sync_api import sync_playwright

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY / profile
    steps = []
    messages = [{"role": "user", "content": f"Goal: {goal}"}]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        )
        page = context.new_page()
        agent = BrowserAgent(page)
        dispatch = {
            "web_search": lambda i: agent.web_search(i["query"]),
            "open_url": lambda i: agent.open_url(i["url"]),
            "read_page": lambda i: agent.read_page(),
            "find_links": lambda i: agent.find_links(),
            "click": lambda i: agent.click(i["text"]),
        }

        final_answer = None
        try:
            for _ in range(MAX_STEPS):
                resp = client.messages.create(
                    model=MODEL,
                    max_tokens=2048,
                    system=SYSTEM_PROMPT,
                    tools=TOOLS,
                    messages=messages,
                )
                messages.append({"role": "assistant", "content": resp.content})

                if resp.stop_reason == "refusal":
                    final_answer = "The request was refused by the safety system."
                    break

                tool_uses = [b for b in resp.content if b.type == "tool_use"]
                # Record any assistant text as a step note.
                for b in resp.content:
                    if b.type == "text" and b.text.strip():
                        steps.append({"thought": b.text.strip()[:500]})

                if not tool_uses:
                    # Model answered in plain text without calling finish.
                    txt = "".join(b.text for b in resp.content if b.type == "text")
                    final_answer = txt.strip() or "(no answer)"
                    break

                results = []
                done = False
                for tu in tool_uses:
                    if tu.name == "finish":
                        final_answer = tu.input.get("answer", "").strip()
                        steps.append({"tool": "finish", "answer": final_answer})
                        done = True
                        results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": tu.id,
                                "content": "Task finished.",
                            }
                        )
                        continue
                    fn = dispatch.get(tu.name)
                    try:
                        out = fn(tu.input) if fn else f"Unknown tool {tu.name}"
                    except Exception as exc:  # keep the loop alive on tool errors
                        out = f"Tool error: {exc}"
                    steps.append(
                        {"tool": tu.name, "input": tu.input, "output": out[:800]}
                    )
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": out,
                        }
                    )
                messages.append({"role": "user", "content": results})
                if done:
                    break

            if final_answer is None:
                final_answer = "Stopped after reaching the step limit without a final answer."
        finally:
            context.close()
            browser.close()

    return {"result": final_answer, "steps": steps}


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------

app = FastAPI(title="BMO PC Agent")
_task_lock = threading.Semaphore(1)  # one browser task at a time (keeps it light)


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "bmo-pc-agent",
        "engine": "playwright+anthropic",
        "model": MODEL,
        "anthropic_key_present": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "headless": HEADLESS,
    }


@app.post("/task")
async def task(request: Request):
    # Parse the body leniently so a bare `curl -d '{"goal":"..."}'` (no
    # Content-Type header) works as well as a proper application/json request.
    goal = ""
    raw = await request.body()
    if raw:
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
            if isinstance(data, dict):
                goal = str(data.get("goal", "") or "")
        except Exception:
            goal = ""
    goal = goal.strip()
    if not goal:
        return JSONResponse(status_code=400, content={"error": "missing 'goal'"})
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return JSONResponse(
            status_code=503,
            content={
                "error": "ANTHROPIC_API_KEY is not set on the PC agent. "
                "Set it in the environment and restart pc_agent.",
                "result": None,
                "steps": [],
            },
        )
    acquired = _task_lock.acquire(blocking=False)
    if not acquired:
        return JSONResponse(
            status_code=429,
            content={"error": "another browser task is already running; retry shortly."},
        )
    try:
        out = await run_in_threadpool(_run_agent_sync, goal)
        return out
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={"error": str(exc), "trace": traceback.format_exc()[-1500:]},
        )
    finally:
        _task_lock.release()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PC_AGENT_PORT", "8200")))
