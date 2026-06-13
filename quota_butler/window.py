"""窗口同一性判断（共享）。

⚠️ 关键：oauth/usage 后端每次现算 `resets_at`，**微秒会漂移**（实测同一窗口
两次读到 ...959751 与 ...777681）。所以判断「是不是同一个 5h 窗口」绝不能用字符串
精确匹配，必须用秒级容差。推送去重（rules）和预热去重（handler）共用此函数，避免两处
逻辑分叉导致重复打扰 / 重复烧 token。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Union

TOLERANCE_SECONDS = 60.0  # 同一窗口内微秒/秒级漂移的容忍上限


def _as_dt(value: Union[str, datetime, None]) -> Optional[datetime]:
    if value is None:
        return None
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def same_window(a: Union[str, datetime, None],
                b: Union[str, datetime, None]) -> bool:
    """两个 resets_at 是否指向同一个窗口（容差内即视为同一）。"""
    try:
        da, db = _as_dt(a), _as_dt(b)
    except (ValueError, TypeError):
        return False
    if da is None or db is None:
        return False
    return abs((da - db).total_seconds()) < TOLERANCE_SECONDS
