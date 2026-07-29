"""Conservative routing for spoken requests."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from enum import Enum


class Intent(str, Enum):
    QUESTION = "question"
    CODING = "coding"
    ACTION = "action"


@dataclass(frozen=True)
class RoutedRequest:
    intent: Intent
    text: str
    language: str
    confidence: float
    project: str | None = None
    requires_confirmation: bool = False
    confirmed: bool = False
    request_id: str = ""


CODING_NOUNS = (
    "代码", "项目", "仓库", "repo", "repository", "bug", "前端", "网页",
    "网站", "组件", "接口", "测试", "code", "coding", "website", "frontend",
)
CODING_VERBS = (
    "写", "改", "修", "实现", "开发", "重构", "运行", "测试", "删除", "删",
    "build", "fix", "implement", "refactor", "edit", "run", "delete", "remove",
    "vibe coding", "vibecoding",
)
STRONG_CODING = (
    "帮我改一下", "帮我修一下", "直接改", "直接修", "开始写", "开始改",
    "动手实现", "go ahead and fix", "go ahead and edit", "make the change",
)
ACTION_NOUNS = (
    "日历", "日程", "会议", "任务", "待办", "提醒", "飞书", "消息", "ddl",
    "邮件", "文档", "灯", "屏幕", "音量", "deadline", "calendar", "meeting",
    "task", "reminder", "schedule", "email", "document", "light", "volume",
)
ACTION_VERBS = (
    "创建", "添加", "安排", "取消", "修改", "删除", "查看", "查一下", "总结",
    "规划", "提醒", "发消息", "发送", "写", "打开", "关闭", "调高", "调低",
    "create", "add", "schedule", "cancel", "update", "delete", "check", "show",
    "plan", "remind", "send", "write", "open", "close", "turn on", "turn off",
)
PERSONAL_LOOKUP = (
    "我的", "我今天", "我明天", "有什么", "有哪些", "几点", "下一场",
    "告诉我", "my ", "do i have", "what's on", "what is on", "when is my",
)
TIME_HINT = re.compile(
    r"(今天|明天|后天|上午|下午|晚上|周[一二三四五六日天]|星期|"
    r"\d{1,2}[:：点]\d{0,2}|today|tomorrow|am|pm|\d{1,2}:\d{2})",
    re.IGNORECASE,
)
HIGH_RISK_TERMS = (
    "删除", "清空", "取消会议", "发消息", "发送邮件", "发布", "部署", "上线",
    "推送", "付款", "购买", "delete", "remove", "cancel meeting", "send message",
    "send email", "publish", "deploy", "push", "purchase", "pay",
)
CJK = re.compile(r"[\u3400-\u9fff]")
LATIN = re.compile(r"[A-Za-z]")
QUESTION_PREFIX = re.compile(
    r"^(?:"
    r"为什么|为何|怎么(?:样)?|如何|什么是|请问|能否告诉我|告诉我怎么|"
    r"how(?:\s+do|\s+does|\s+did|\s+can|\s+should|\s+to|\s+would|\b)|"
    r"why\b|what\b|when\b|where\b|who\b|which\b|"
    r"can\s+i\b|could\s+i\b|should\s+i\b|is\s+it\b|are\s+there\b"
    r")",
    re.IGNORECASE,
)


def _has_pair(text: str, nouns: tuple[str, ...], verbs: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(noun in lowered for noun in nouns) and any(
        verb in lowered for verb in verbs
    )


def detect_language(text: str) -> str:
    has_cjk = bool(CJK.search(text))
    has_latin = bool(LATIN.search(text))
    if has_cjk and has_latin:
        return "mixed"
    if has_cjk:
        return "zh"
    if has_latin:
        return "en"
    return "unknown"


def _request(
    intent: Intent,
    text: str,
    confidence: float,
) -> RoutedRequest:
    lowered = text.casefold()
    return RoutedRequest(
        intent=intent,
        text=text,
        language=detect_language(text),
        confidence=confidence,
        requires_confirmation=any(term in lowered for term in HIGH_RISK_TERMS),
        request_id=f"voice-{uuid.uuid4().hex[:12]}",
    )


def route_request(text: str) -> RoutedRequest:
    """Prefer question mode unless the user explicitly asks for execution."""

    cleaned = text.strip()
    # A noun/verb pair alone does not prove execution intent. How-to
    # questions such as “如何删除 Git 分支” mention the same words as a
    # command, but must stay in read-only GPT question mode.
    if QUESTION_PREFIX.search(cleaned):
        return _request(Intent.QUESTION, cleaned, 0.96)
    if _has_pair(cleaned, CODING_NOUNS, CODING_VERBS):
        return _request(Intent.CODING, cleaned, 0.98)
    if _has_pair(cleaned, ACTION_NOUNS, ACTION_VERBS):
        return _request(Intent.ACTION, cleaned, 0.98)
    lowered = cleaned.casefold()
    if any(phrase in lowered for phrase in STRONG_CODING) and not any(
        noun in lowered for noun in ACTION_NOUNS
    ):
        return _request(Intent.CODING, cleaned, 0.88)
    if any(noun in lowered for noun in ACTION_NOUNS) and any(
        hint in lowered for hint in PERSONAL_LOOKUP
    ):
        return _request(Intent.ACTION, cleaned, 0.86)
    if (
        TIME_HINT.search(cleaned)
        and any(verb in lowered for verb in ACTION_VERBS)
        and ("会" in cleaned or "约" in cleaned)
    ):
        return _request(Intent.ACTION, cleaned, 0.9)
    return _request(Intent.QUESTION, cleaned, 0.62 if cleaned else 0.0)


def codex_envelope(request: RoutedRequest) -> str:
    policies = {
        Intent.QUESTION: (
            "Answer as GPT in the user's language. This is a question turn: do not "
            "edit files, run commands, or call external tools."
        ),
        Intent.CODING: (
            "This is an explicit coding instruction. Work only inside the configured "
            "project root, inspect before editing, run relevant tests, and summarize "
            "the result. Ask which project if the target cannot be determined. Never "
            "deploy, push, broadly delete, or use secrets without explicit consent."
        ),
        Intent.ACTION: (
            "This is an explicit function request. Use installed Lark skills and "
            "lark-cli as user when relevant. Read-only actions may proceed. For writes, "
            "the spoken request authorizes only its exact scope; resolve ambiguity and "
            "honor every lark-cli confirmation gate. Never append --yes unless the user "
            "explicitly confirms after the gate is shown."
        ),
    }
    return (
        f"<voice_intent>{request.intent.value}</voice_intent>\n"
        f"<request_id>{request.request_id}</request_id>\n"
        f"<language>{request.language}</language>\n"
        f"<confidence>{request.confidence:.2f}</confidence>\n"
        f"<project>{request.project or ''}</project>\n"
        f"<confirmed>{str(request.confirmed).lower()}</confirmed>\n"
        f"<policy>{policies[request.intent]}</policy>\n"
        f"<user_utterance>{request.text}</user_utterance>"
    )
