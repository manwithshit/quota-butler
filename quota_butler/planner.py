"""Deterministic V3 tomorrow-plan calculator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, List, Mapping, Sequence, Tuple

from .providers.base import Usage
from .schedule_flow import PlanRequest, normalize_hhmm

DEFAULT_WINDOW_SECONDS = 5 * 3600
SUPPORTED_AGENTS = ("cc", "codex")
AGENT_LABELS = {"cc": "Claude Code", "codex": "Codex"}


@dataclass(frozen=True)
class PlanEvent:
    agent: str
    kind: str
    at: datetime
    purpose: str

    @property
    def note(self) -> str:
        return self.purpose


@dataclass(frozen=True)
class SchedulePlan:
    agents: Tuple[str, ...]
    work_start: datetime
    work_end: datetime
    events: Tuple[PlanEvent, ...]
    reason: str
    request: PlanRequest
    plan_version: int = 3

    @property
    def work_hours(self) -> float:
        return (self.work_end - self.work_start).total_seconds() / 3600


def build_plan(
    request: PlanRequest,
    available_usages: Mapping[str, Usage],
) -> SchedulePlan:
    start = _combine(request, request.work_start)
    end = _combine(request, request.work_end)
    if end <= start:
        raise ValueError("工作结束时间必须晚于开始时间")
    selected = _select_agents(
        request.agent_strategy,
        available_usages,
        (end - start).total_seconds() / 3600,
    )

    first_agent = selected[0]
    first_warmup = start - timedelta(hours=2, minutes=30)
    second_warmup = _real_reset_in_range(
        available_usages[first_agent],
        start,
        end,
    ) or first_warmup + timedelta(hours=5)
    events: List[PlanEvent] = [
        PlanEvent(first_agent, "warmup", first_warmup, "准备第一个窗口"),
        PlanEvent(first_agent, "warmup", second_warmup, "恢复后准备第二个窗口"),
    ]

    if len(selected) == 2:
        relay_at = max(start + timedelta(hours=5) - timedelta(minutes=10), start)
        if relay_at < end:
            events.append(
                PlanEvent(selected[1], "warmup", relay_at, "为长时间工作准备接力窗口")
            )
        reason = (
            f"前半段优先保持 {AGENT_LABELS[first_agent]} 连续工作，"
            f"后半段由 {AGENT_LABELS[selected[1]]} 接力。"
        )
    else:
        reason = (
            f"当前区间优先使用单 Agent：{AGENT_LABELS[first_agent]}，"
            "减少上下文切换。"
        )
    events.sort(key=lambda item: (item.at, item.agent))
    return SchedulePlan(
        agents=selected,
        work_start=start,
        work_end=end,
        events=tuple(events),
        reason=reason,
        request=request,
    )


def parse_agents(value: object) -> Tuple[str, ...]:
    if isinstance(value, str):
        raw = value.replace("，", ",").split(",")
    elif isinstance(value, Iterable):
        raw = list(value)
    else:
        raw = []
    agents: List[str] = []
    for item in raw:
        agent = _normalize_agent(str(item))
        if agent not in agents:
            agents.append(agent)
    return tuple(agents)


def _select_agents(
    strategy: str,
    usages: Mapping[str, Usage],
    work_hours: float,
) -> Tuple[str, ...]:
    available = tuple(agent for agent in SUPPORTED_AGENTS if agent in usages)
    if not available:
        raise ValueError("当前没有可用于规划的 Agent")
    if strategy in ("cc", "codex"):
        if strategy not in usages:
            raise ValueError(f"{AGENT_LABELS[strategy]} 当前不可用")
        return (strategy,)
    if strategy == "both":
        if len(available) < 2:
            raise ValueError("Claude Code + Codex 当前不能同时使用")
        return _rank_agents(available, usages)
    ranked = _rank_agents(available, usages)
    if work_hours > 5 and len(ranked) > 1:
        return ranked
    return (ranked[0],)


def _rank_agents(
    agents: Sequence[str],
    usages: Mapping[str, Usage],
) -> Tuple[str, ...]:
    return tuple(
        sorted(
            agents,
            key=lambda agent: (
                usages[agent].five_hour.utilization,
                SUPPORTED_AGENTS.index(agent),
            ),
        )
    )


def _real_reset_in_range(
    usage: Usage,
    start: datetime,
    end: datetime,
):
    reset = usage.five_hour.resets_at
    if reset is None:
        return None
    if reset.tzinfo is not None:
        reset = reset.astimezone().replace(tzinfo=None)
    if start <= reset <= end:
        return reset
    return None


def _combine(request: PlanRequest, hhmm: str) -> datetime:
    normalized = normalize_hhmm(hhmm)
    hour, minute = (int(part) for part in normalized.split(":"))
    return datetime.combine(
        request.target_date,
        datetime.min.time().replace(hour=hour, minute=minute),
    )


def _normalize_agent(value: str) -> str:
    key = value.strip().lower()
    if key in ("claude", "claude-code", "claude code"):
        key = "cc"
    if key not in SUPPORTED_AGENTS:
        raise ValueError(f"unsupported scheduler agent: {value!r}")
    return key
