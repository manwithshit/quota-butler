"""S2 · 触发规则 + 去重。

主规则「窗口换挡」：resets_at - now < RESET_SOON_MIN → 触发。
可选叠加：utilization < WASTE_PCT（剩太多没用 = 防浪费味）。
去重：同一个 resets_at 窗口只提醒一次。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .config import Config
from .providers.base import Usage
from .state import State


@dataclass
class Decision:
    notify: bool
    reason: str                       # 人话理由，便于日志 / 卡片提示
    minutes_to_reset: Optional[float] = None


def should_notify(usage: Usage, state: State, config: Config,
                  now: Optional[datetime] = None) -> Decision:
    now = now or datetime.now(timezone.utc)
    five = usage.five_hour
    resets_at = five.resets_at
    if resets_at.tzinfo is None:
        resets_at = resets_at.replace(tzinfo=timezone.utc)

    minutes = (resets_at - now).total_seconds() / 60.0

    # 主规则：是否临近换挡
    if minutes >= config.reset_soon_min:
        return Decision(False, f"距 reset 还有 {minutes:.0f} 分钟，未到阈值", minutes)

    # 已经 reset 过去了（负数）也算未命中本窗口提醒语义
    if minutes < 0:
        return Decision(False, "窗口已重置", minutes)

    # 可选叠加：防浪费
    if config.waste_pct is not None and five.utilization >= config.waste_pct:
        return Decision(
            False,
            f"利用率 {five.utilization:.0f}% ≥ 防浪费阈值 {config.waste_pct:.0f}%，不提醒",
            minutes,
        )

    # 去重：同一个 resets_at 已经提醒过
    if state.last_notified_reset_at and _same_window(
        state.last_notified_reset_at, resets_at
    ):
        return Decision(False, "该窗口已提醒过（去重）", minutes)

    return Decision(
        True,
        f"5h 窗口 {minutes:.0f} 分钟后重置，已用 {five.utilization:.0f}%",
        minutes,
    )


def _same_window(stored_iso: str, resets_at: datetime) -> bool:
    try:
        stored = datetime.fromisoformat(stored_iso)
    except ValueError:
        return False
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=timezone.utc)
    return abs((stored - resets_at).total_seconds()) < 1.0
