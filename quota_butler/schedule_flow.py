"""Pure data model for the guided schedule-card flow."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Dict, Mapping, Optional

FLOW_VERSION = 3

TASK_TYPE_ALIASES = {
    "coding": "coding",
    "编码开发": "coding",
    "content": "content",
    "内容创作": "content",
    "research": "research",
    "调研分析": "research",
    "mixed": "mixed",
    "混合任务": "mixed",
}

INTENSITY_ALIASES = {
    "light": "light",
    "轻量": "light",
    "normal": "normal",
    "正常": "normal",
    "high": "high",
    "高强度": "high",
}

TASK_TYPE_LABELS = {
    "coding": "编码开发",
    "content": "内容创作",
    "research": "调研分析",
    "mixed": "混合任务",
}

INTENSITY_LABELS = {
    "light": "轻量",
    "normal": "正常",
    "high": "高强度",
}


@dataclass(frozen=True)
class SchedulePreferences:
    task_type: str = "mixed"
    intensity: str = "normal"
    work_start: str = "09:00"
    work_end: str = "17:00"
    daily_scenario: str = ""


def parse_preferences(value: Optional[Mapping[str, Any]]) -> SchedulePreferences:
    raw = value or {}
    task_type = _normalize_choice(
        raw.get("task_type", "mixed"),
        TASK_TYPE_ALIASES,
        "任务类型",
    )
    intensity = _normalize_choice(
        raw.get("intensity", "normal"),
        INTENSITY_ALIASES,
        "工作强度",
    )
    work_start = _normalize_hhmm(raw.get("work_start", "09:00"))
    work_end = _normalize_hhmm(raw.get("work_end", "17:00"))
    daily_scenario = str(raw.get("daily_scenario") or "").strip()
    if len(daily_scenario) > 120:
        raise ValueError("日常使用场景不能超过 120 个字符")
    validate_work_time(work_start, work_end)
    return SchedulePreferences(
        task_type,
        intensity,
        work_start,
        work_end,
        daily_scenario,
    )


def validate_work_time(work_start: str, work_end: str) -> int:
    start_minutes = _minutes(_normalize_hhmm(work_start))
    end_minutes = _minutes(_normalize_hhmm(work_end))
    duration = end_minutes - start_minutes
    if duration <= 0:
        raise ValueError("工作结束时间必须晚于开始时间")
    if duration > 16 * 60:
        raise ValueError("工作时段不能超过 16 小时")
    return duration


def flow_payload(
    *,
    step: str,
    target_date: date,
    preferences: SchedulePreferences,
    **fields: Any,
) -> Dict[str, Any]:
    return {
        "cmd": "quota",
        "action": "schedule_flow",
        "flow_version": FLOW_VERSION,
        "step": step,
        "target_date": target_date.isoformat(),
        "preferences": asdict(preferences),
        **fields,
    }


def validate_flow_context(
    payload: Mapping[str, Any],
    *,
    today: Optional[date] = None,
) -> date:
    if payload.get("flow_version") != FLOW_VERSION:
        raise ValueError("规划卡版本已失效")
    try:
        target = date.fromisoformat(str(payload.get("target_date") or ""))
    except ValueError as exc:
        raise ValueError("规划日期无效") from exc
    if target < (today or date.today()):
        raise ValueError("规划卡已过期")
    return target


def _normalize_choice(value: Any, aliases: Mapping[str, str], label: str) -> str:
    normalized = aliases.get(str(value).strip())
    if normalized is None:
        raise ValueError(f"{label}无效")
    return normalized


def _normalize_hhmm(value: Any) -> str:
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


def _minutes(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return (hour * 60) + minute
