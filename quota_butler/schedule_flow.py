"""Pure V3 request model for tomorrow-plan cards."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, Mapping, Optional

FLOW_VERSION = 4
TIME_MODES = ("point", "range")
AGENT_STRATEGIES = ("auto", "cc", "codex", "both")


@dataclass(frozen=True)
class PlanRequest:
    target_date: date
    time_mode: str = "point"
    work_start: str = "09:00"
    work_end: str = "14:00"
    agent_strategy: str = "auto"


def parse_plan_request(
    value: Optional[Mapping[str, Any]],
    *,
    available_agent_count: int,
) -> PlanRequest:
    raw = dict(value or {})
    try:
        target_date = date.fromisoformat(str(raw.get("target_date") or ""))
    except ValueError as exc:
        raise ValueError("规划日期无效") from exc
    time_mode = str(raw.get("time_mode") or "point").strip().lower()
    if time_mode not in TIME_MODES:
        raise ValueError("时间模式无效")
    strategy = str(raw.get("agent_strategy") or "auto").strip().lower()
    if strategy not in AGENT_STRATEGIES:
        raise ValueError("AI 工具选择无效")
    work_start = normalize_hhmm(raw.get("work_start") or "09:00")
    if time_mode == "point":
        duration = 8 if available_agent_count >= 2 else 5
        work_end = _add_hours(work_start, duration)
    else:
        work_end = normalize_hhmm(raw.get("work_end") or "")
        validate_work_time(work_start, work_end)
    return PlanRequest(
        target_date=target_date,
        time_mode=time_mode,
        work_start=work_start,
        work_end=work_end,
        agent_strategy=strategy,
    )


def flow_payload(step: str, request: PlanRequest, **fields: Any) -> Dict[str, Any]:
    request_dict = asdict(request)
    request_dict["target_date"] = request.target_date.isoformat()
    return {
        "cmd": "quota",
        "action": "schedule_flow",
        "flow_version": FLOW_VERSION,
        "step": step,
        "target_date": request.target_date.isoformat(),
        "request": request_dict,
        **fields,
    }


def validate_flow_context(
    payload: Mapping[str, Any],
    *,
    today: Optional[date] = None,
) -> date:
    if payload.get("flow_version") != FLOW_VERSION:
        raise ValueError("该卡片已失效，请重新打开菜单")
    try:
        target = date.fromisoformat(str(payload.get("target_date") or ""))
    except ValueError as exc:
        raise ValueError("规划日期无效") from exc
    if target < (today or date.today()):
        raise ValueError("该卡片已过期，请重新规划")
    return target


def normalize_hhmm(value: Any) -> str:
    text = str(value).strip().split()[0]
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError("时间格式必须为 HH:mm")
    try:
        hour, minute = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError("时间格式必须为 HH:mm") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("时间格式必须为 HH:mm")
    return f"{hour:02d}:{minute:02d}"


def validate_work_time(work_start: str, work_end: str) -> int:
    start = _minutes(normalize_hhmm(work_start))
    end = _minutes(normalize_hhmm(work_end))
    duration = end - start
    if duration <= 0:
        raise ValueError("结束时间必须晚于开始时间")
    if duration > 16 * 60:
        raise ValueError("重度使用区间不能超过 16 小时")
    return duration


def _add_hours(value: str, hours: int) -> str:
    start = datetime.combine(date.today(), datetime.strptime(value, "%H:%M").time())
    end = start + timedelta(hours=hours)
    if end.date() != start.date():
        raise ValueError("默认计划不能跨天")
    return end.strftime("%H:%M")


def _minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 60 + minute
