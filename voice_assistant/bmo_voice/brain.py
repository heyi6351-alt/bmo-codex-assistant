"""High-level assistant coordinator.

Ties together ordinary questions, explicit actions, high-risk confirmations,
background coding jobs, progress queries, cancellation, retry, and proactive
reminder preferences.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from .codex import CodexBrain
from .config import Settings
from .events import EventSink
from .intent import Intent, RoutedRequest, route_request
from .jobs import CodexJobManager
from .proactive import ProactiveScheduler

LOG = logging.getLogger(__name__)

_CONFIRM_YES = frozenset(
    {
        "确认",
        "确认执行",
        "确定",
        "确定执行",
        "是的",
        "好的",
        "好",
        "可以",
        "行",
        "yes",
        "ok",
        "sure",
        "goahead",
    }
)
_CONFIRM_NO_CONTAINS = (
    "取消",
    "不用",
    "不要",
    "算了",
    "不确定",
    "不确认",
    "先不要",
    "先别",
    "等等",
    "等一下",
    "不行",
    "不好",
    "不对",
    "再想想",
    "别执行",
)
_CONFIRM_NO_PREFIXES = ("no", "nope", "cancel", "stop")

# Progress queries must reference the job clearly enough that ordinary action
# requests such as "帮我创建一个任务" are never hijacked into a status reply.
_PROGRESS_TERMS = ("进度", "做到哪", "改好了吗", "写好了吗", "status", "progress")
_JOB_TERMS = ("job", "coding", "任务", "代码", "项目")
_QUERY_TERMS = ("怎么样", "如何", "完成了吗", "做完了吗", "状态", "done", "finished")


class AssistantBrain:
    """Single entry point for a spoken turn.

    Implements the ``Brain.ask`` protocol used by ``ConversationController``
    while also exposing confirmation state and job notification polling.
    """

    def __init__(
        self,
        settings: Settings,
        events: EventSink,
        codex: CodexBrain,
        jobs: CodexJobManager,
        scheduler: ProactiveScheduler,
        *,
        clock=time.monotonic,
    ):
        self.settings = settings
        self.events = events
        self.codex = codex
        self.jobs = jobs
        self.scheduler = scheduler
        self.clock = clock
        self.awaiting_confirmation = False
        self._pending: RoutedRequest | None = None
        self._pending_since: float | None = None

    def ask(self, transcript: str) -> str:
        """Handle one user utterance and return the spoken reply."""

        normalized = transcript.strip()
        if not normalized:
            return "我没听清，请再说一次。"

        if self._pending is not None and self._confirmation_expired():
            self._clear_pending()
            self.events.emit("idle")
            if self._looks_like_confirmation_answer(normalized):
                return "刚才的确认已超时，操作已取消。请重新告诉我需要做什么。"

        if self._pending is not None:
            return self._resolve_confirmation(normalized)

        preference = self._handle_reminder_preferences(normalized)
        if preference:
            return preference

        if self._is_cancel(normalized):
            return self.jobs.cancel()

        if self._is_retry(normalized):
            job, problem = self.jobs.retry_last()
            if job is None:
                return problem
            return f"已重新提交 {job.job_id}，项目 {Path(job.project).name}。"

        if self._is_progress_query(normalized):
            return self.jobs.status_text()

        request = route_request(normalized)

        if request.intent == Intent.CODING:
            return self._handle_coding(request)

        if request.intent == Intent.ACTION:
            return self._handle_action(request)

        return self._handle_question(request)

    def _handle_question(self, request: RoutedRequest) -> str:
        # ConversationController owns the per-turn thinking/speaking events;
        # the brain only emits specialized events to avoid duplicates.
        return self._ask_codex(request)

    def _handle_action(self, request: RoutedRequest) -> str:
        if request.requires_confirmation and not request.confirmed:
            self._arm_confirmation(request)
            return "这条操作涉及写入或外部通讯，请说「确认」以继续，或说「取消」。"

        self.events.emit(
            "tool_call",
            intent=request.intent.value,
            transcript=request.text,
            confidence=request.confidence,
            subtitle="正在调用办公能力",
        )
        return self._ask_codex(request)

    def _handle_coding(self, request: RoutedRequest) -> str:
        if request.requires_confirmation and not request.confirmed:
            self._arm_confirmation(request)
            return "这条 coding 任务涉及删除、发布等高风险操作，请说「确认」以继续，或说「取消」。"

        project_hint = request.project
        if project_hint is None:
            latest = self.jobs.latest()
            if latest is not None:
                project_hint = Path(latest.project).name
        job, problem = self.jobs.submit(
            request.text,
            project_hint,
            request_id=request.request_id,
            confirmed=request.confirmed,
        )
        if job is None:
            return problem
        self.events.emit(
            "coding",
            intent=request.intent.value,
            transcript=request.text,
            confidence=request.confidence,
            job=job.public(),
            subtitle=f"{Path(job.project).name}：任务已提交",
        )
        return (
            f"已提交后台任务 {job.job_id}，项目 {Path(job.project).name}。"
            "你可以随时询问进度。"
        )

    def _arm_confirmation(self, request: RoutedRequest) -> None:
        self._pending = request
        self._pending_since = self.clock()
        self.awaiting_confirmation = True

    def _clear_pending(self) -> None:
        self._pending = None
        self._pending_since = None
        self.awaiting_confirmation = False

    def _confirmation_expired(self) -> bool:
        if self._pending_since is None:
            return False
        return (
            self.clock() - self._pending_since
            > self.settings.confirmation_timeout_seconds
        )

    @staticmethod
    def _confirmation_decision(transcript: str) -> bool | None:
        normalized = re.sub(
            r"""[\s，。！？、,.!?;；:："”'‘’]""",
            "",
            transcript.casefold(),
        )
        if any(phrase in normalized for phrase in _CONFIRM_NO_CONTAINS):
            return False
        if normalized == "否" or normalized.startswith(_CONFIRM_NO_PREFIXES):
            return False
        if normalized in _CONFIRM_YES:
            return True
        return None

    @classmethod
    def _looks_like_confirmation_answer(cls, transcript: str) -> bool:
        return cls._confirmation_decision(transcript) is not None

    def _resolve_confirmation(self, transcript: str) -> str:
        decision = self._confirmation_decision(transcript)
        if decision is False:
            self._clear_pending()
            self.events.emit("idle")
            return "已取消。"

        if decision is True:
            request = self._pending
            self._clear_pending()
            if request is None:
                return "确认已处理。"
            confirmed = RoutedRequest(
                intent=request.intent,
                text=request.text,
                language=request.language,
                confidence=request.confidence,
                project=request.project,
                requires_confirmation=request.requires_confirmation,
                confirmed=True,
                request_id=request.request_id,
            )
            if confirmed.intent == Intent.CODING:
                return self._handle_coding(confirmed)
            return self._handle_action(confirmed)

        return "请说「确认」或「取消」。"

    def _ask_codex(self, request: RoutedRequest) -> str:
        ask_request = getattr(self.codex, "ask_request", None)
        if callable(ask_request):
            return str(ask_request(request))
        return self.codex.ask(request.text)

    def _handle_reminder_preferences(self, transcript: str) -> str | None:
        lowered = transcript.casefold()
        if any(
            phrase in lowered
            for phrase in (
                "今天别提醒我",
                "今天不要提醒",
                "今天不提醒",
                "do not remind me today",
                "no reminders today",
            )
        ):
            self.scheduler.suppress_today()
            self.events.emit("reminder", subtitle="今日提醒已关闭")
            return "好的，今天不再主动提醒。恢复时请说「恢复提醒」。"

        if any(
            phrase in lowered
            for phrase in (
                "恢复提醒",
                "resume reminders",
                "remind me again",
                "重新开始提醒",
            )
        ):
            self.scheduler.resume_reminders()
            self.events.emit("reminder", subtitle="提醒已恢复")
            return "提醒已恢复。"

        match = re.search(r"(?:推迟|延迟|snooze).*?(\d+)\s*(?:分钟|min|minutes?)", lowered)
        if match:
            minutes = int(match.group(1))
            self.scheduler.snooze(minutes)
            self.events.emit("reminder", subtitle=f"提醒已推迟 {minutes} 分钟")
            return f"提醒已推迟 {minutes} 分钟。"

        return None

    @staticmethod
    def _is_progress_query(text: str) -> bool:
        lowered = text.casefold()
        if any(term in lowered for term in _PROGRESS_TERMS):
            return True
        return any(term in lowered for term in _JOB_TERMS) and any(
            term in lowered for term in _QUERY_TERMS
        )

    @staticmethod
    def _is_cancel(text: str) -> bool:
        lowered = text.casefold()
        return any(
            phrase in lowered
            for phrase in (
                "取消任务",
                "取消当前任务",
                "cancel job",
                "cancel the job",
                "停止任务",
                "stop the job",
                "终止任务",
            )
        )

    @staticmethod
    def _is_retry(text: str) -> bool:
        lowered = text.casefold()
        return any(
            phrase in lowered
            for phrase in (
                "重试",
                "再来一次",
                "重新执行",
                "retry",
                "try again",
                "重新运行",
                "再做一次",
            )
        )

    def poll_notifications(self) -> list[str]:
        """Return queued job completion/cancellation messages to speak."""

        return self.jobs.drain_notifications()
