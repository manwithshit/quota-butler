"""V2 AI Agent Scheduler.

The planner turns a lightweight work profile into a deterministic warmup plan.
It is intentionally pure: no provider calls, no Feishu calls, and no state file
updates. Runtime entrypoints can render or execute the resulting plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Iterable, List, Optional, Sequence, Tuple

from .config import Config

DEFAULT_WINDOW_SECONDS = 5 * 3600
SUPPORTED_AGENTS = ("cc", "codex")

MODE_ALIASES = {
    "不断粮": "sustain",
    "不断粮模式": "sustain",
    "sustain": "sustain",
    "no-hunger": "sustain",
    "冲刺": "sustain",
    "今天冲刺": "sustain",
    "平衡": "balanced",
    "平衡模式": "balanced",
    "balanced": "balanced",
    "节省": "savings",
    "节省模式": "savings",
    "savings": "savings",
    "save": "savings",
}


@dataclass(frozen=True)
class PlanEvent:
    agent: str
    kind: str
    at: datetime
    note: str = ""


@dataclass(frozen=True)
class SchedulePlan:
    mode: str
    agents: Tuple[str, ...]
    work_start: datetime
    work_end: datetime
    cas: float
    waiting_minutes: float
    events: Tuple[PlanEvent, ...]

    @property
    def work_hours(self) -> float:
        return (self.work_end - self.work_start).total_seconds() / 3600.0


def plan_from_config(
    cfg: Config,
    *,
    intent: Optional[str] = None,
    target_date: Optional[date] = None,
) -> SchedulePlan:
    mode = normalize_mode(intent or cfg.scheduler_mode)
    agents = parse_agents(cfg.scheduler_agents)
    start = combine_local(target_date or date.today(), cfg.work_start)
    sleep = combine_local(target_date or date.today(), cfg.sleep_time)
    if sleep <= start:
        sleep += timedelta(days=1)
    duration_end = start + timedelta(hours=cfg.work_duration_hours)
    end = min(duration_end, sleep)
    if end <= start:
        end = start + timedelta(minutes=1)
    return build_plan(mode=mode, agents=agents, work_start=start, work_end=end)


def build_plan(
    *,
    mode: str,
    agents: Sequence[str],
    work_start: datetime,
    work_end: datetime,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
) -> SchedulePlan:
    mode = normalize_mode(mode)
    agents = tuple(_normalize_agent(a) for a in agents)
    if not agents:
        raise ValueError("at least one scheduler agent is required")
    if work_end <= work_start:
        raise ValueError("work_end must be after work_start")

    window = timedelta(seconds=window_seconds)
    lead = _lead_time(mode, window)
    stagger = window / len(agents)

    events: List[PlanEvent] = []
    intervals: List[Tuple[datetime, datetime]] = []
    horizon = work_end + window

    for idx, agent in enumerate(agents):
        warmup_at = work_start - lead + (stagger * idx if len(agents) > 1 else timedelta())
        if warmup_at > work_start:
            warmup_at = work_start
        events.append(PlanEvent(agent, "warmup", warmup_at, "预热窗口"))

        cursor = warmup_at
        first_recovery = warmup_at + window
        while cursor < horizon:
            interval_end = cursor + window
            intervals.append((max(cursor, work_start), min(interval_end, work_end)))
            if first_recovery <= horizon:
                events.append(PlanEvent(agent, "recovery", first_recovery, "预计恢复"))
            cursor = interval_end
            first_recovery = cursor + window

    coverage = _covered_seconds(intervals, work_start, work_end)
    total = (work_end - work_start).total_seconds()
    waiting = max(total - coverage, 0.0) / 60.0
    cas = 0.0 if total <= 0 else min(coverage / total, 1.0)
    events.sort(key=lambda e: (e.at, e.agent, e.kind))
    return SchedulePlan(
        mode=mode,
        agents=agents,
        work_start=work_start,
        work_end=work_end,
        cas=cas,
        waiting_minutes=waiting,
        events=tuple(events),
    )


def normalize_mode(value: str) -> str:
    key = (value or "balanced").strip().lower()
    return MODE_ALIASES.get(key, key if key in {"sustain", "balanced", "savings"} else "balanced")


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
    return tuple(agents or ("cc",))


def combine_local(day: date, hhmm: str) -> datetime:
    return datetime.combine(day, parse_hhmm(hhmm))


def parse_hhmm(value: str) -> time:
    hh, _, mm = str(value).strip().partition(":")
    return time(int(hh), int(mm or 0))


def _normalize_agent(value: str) -> str:
    key = value.strip().lower()
    if key in ("claude", "claude-code", "claude code"):
        key = "cc"
    if key not in SUPPORTED_AGENTS:
        raise ValueError(f"unsupported scheduler agent: {value!r}")
    return key


def _lead_time(mode: str, window: timedelta) -> timedelta:
    if mode == "sustain":
        return window
    if mode == "savings":
        return timedelta(minutes=30)
    return window / 2


def _covered_seconds(
    intervals: Sequence[Tuple[datetime, datetime]],
    start: datetime,
    end: datetime,
) -> float:
    clipped = sorted((max(a, start), min(b, end)) for a, b in intervals if b > start and a < end)
    if not clipped:
        return 0.0

    total = 0.0
    cur_start, cur_end = clipped[0]
    for item_start, item_end in clipped[1:]:
        if item_start <= cur_end:
            cur_end = max(cur_end, item_end)
            continue
        total += (cur_end - cur_start).total_seconds()
        cur_start, cur_end = item_start, item_end
    total += (cur_end - cur_start).total_seconds()
    return total
