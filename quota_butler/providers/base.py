"""Provider 接口与统一数据结构。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


class ProviderError(Exception):
    """provider 读取额度 / 预热失败的规范异常。"""


@dataclass
class WindowUsage:
    """一个额度窗口的快照。"""
    utilization: float            # 已用百分比 0–100
    resets_at: Optional[datetime] # 该窗口重置的绝对时间（带时区）
    window_seconds: Optional[int] = None  # 窗口时长（秒），用于区分 5h / 7天 / 月度
    kind: str = ""                # five_hour / weekly / monthly / unknown


@dataclass
class Usage:
    """一次感知的统一结果。"""
    provider: str                 # "cc"
    five_hour: WindowUsage
    seven_day: Optional[WindowUsage] = None


class Provider:
    """感知 + 预热的统一接口。"""

    name: str = "base"

    def read_usage(self) -> Usage:
        raise NotImplementedError

    def warmup(self, prompt: str) -> str:
        """发一条预热消息开窗，返回简短回执文本。"""
        raise NotImplementedError
