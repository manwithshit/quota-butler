"""Pure decision rules for proactive Agent recovery notifications."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .config import Config
from .providers.base import Usage
from .state import State


@dataclass(frozen=True)
class OneUpDecision:
    notify: bool
    reason: str
    provider: Optional[str] = None
    window_key: Optional[str] = None


def should_offer_oneup(
    provider: str,
    usage: Usage,
    state: State,
    config: Config,
    *,
    now: Optional[datetime] = None,
) -> OneUpDecision:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if state.active_plan and state.active_plan.get("status") == "active":
        return OneUpDecision(False, "已有生效计划")
    if config.muted or config.is_quiet(now):
        return OneUpDecision(False, "当前静音或处于安静时段")
    if _is_snoozed(state.muted_until, now):
        return OneUpDecision(False, "用户已延后提醒")

    pending = state.pending_oneup or {}
    if pending.get("provider") == provider and usage.five_hour.utilization <= 5:
        key = str(pending.get("window_key") or "")
        if key:
            return OneUpDecision(True, "延后时间已到", provider, key)

    snapshots = state.provider_snapshots or {}
    previous = snapshots.get(provider) or {}
    previous_reset_raw = previous.get("reset_at")
    previous_reset = _parse_datetime(previous_reset_raw)
    if previous_reset is None:
        return OneUpDecision(False, "尚无上一窗口基线")
    if previous_reset > now:
        return OneUpDecision(False, "上一窗口尚未恢复")
    if usage.five_hour.utilization > 5:
        return OneUpDecision(False, "恢复后已有明显使用，不再主动打扰")

    key = f"{provider}:{previous_reset_raw}"
    if state.last_oneup_notified_window == key:
        return OneUpDecision(False, "该恢复窗口已提醒过", provider, key)
    return OneUpDecision(True, "窗口已恢复且当前可用", provider, key)


def _is_snoozed(value: Optional[str], now: datetime) -> bool:
    muted_until = _parse_datetime(value)
    return muted_until is not None and muted_until > now


def _parse_datetime(value: object) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
