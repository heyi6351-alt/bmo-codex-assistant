"""Conservative routing for spoken requests."""

from __future__ import annotations

import re
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


CODING_NOUNS = (
    "代码", "项目", "仓库", "repo", "repository", "bug", "前端", "网页",
    "网站", "组件", "接口", "测试", "code", "coding", "website", "frontend",
)
CODING_VERBS = (
    "写", "改", "修", "实现", "开发", "重构", "运行", "测试", "build", "fix",
    "implement", "refactor", "edit", "run", "vibe coding", "vibecoding",
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
    "规划", "提醒", "发消息", "打开", "关闭", "调高", "调低", "create", "add",
    "schedule", "cancel", "update", "delete", "check", "show", "plan", "remind",
    "send", "open", "close", "turn on", "turn off",
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


def _has_pair(text: str, nouns: tuple[str, ...], verbs: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(noun in lowered for noun in nouns) and any(
        verb in lowered for verb in verbs
    )


def route_request(text: str) -> RoutedRequest:
    """Prefer question mode unless the user explicitly asks for execution."""

    cleaned = text.strip()
    if _has_pair(cleaned, CODING_NOUNS, CODING_VERBS):
        return RoutedRequest(Intent.CODING, cleaned)
    if _has_pair(cleaned, ACTION_NOUNS, ACTION_VERBS):
        return RoutedRequest(Intent.ACTION, cleaned)
    lowered = cleaned.casefold()
    if any(phrase in lowered for phrase in STRONG_CODING) and not any(
        noun in lowered for noun in ACTION_NOUNS
    ):
        return RoutedRequest(Intent.CODING, cleaned)
    if any(noun in lowered for noun in ACTION_NOUNS) and any(
        hint in lowered for hint in PERSONAL_LOOKUP
    ):
        return RoutedRequest(Intent.ACTION, cleaned)
    if (
        TIME_HINT.search(cleaned)
        and any(verb in lowered for verb in ACTION_VERBS)
        and ("会" in cleaned or "约" in cleaned)
    ):
        return RoutedRequest(Intent.ACTION, cleaned)
    return RoutedRequest(Intent.QUESTION, cleaned)


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
        f"<policy>{policies[request.intent]}</policy>\n"
        f"<user_utterance>{request.text}</user_utterance>"
    )
