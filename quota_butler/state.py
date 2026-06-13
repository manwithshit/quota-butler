"""本地状态文件（JSON）读写。用于去重 / 防重复打扰。不上 SQLite。

字段：
  last_run_at            上次运行时间（ISO8601）
  last_utilization       上次读到的 5h 利用率
  last_reset_at          上次读到的 5h resets_at
  last_notified_reset_at 上次「已推送提醒」对应的 resets_at —— 去重的关键
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class State:
    last_run_at: Optional[str] = None
    last_utilization: Optional[float] = None
    last_reset_at: Optional[str] = None
    last_notified_reset_at: Optional[str] = None


def load(path: str) -> State:
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return State()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # 状态文件损坏不应让哨兵崩；当作空状态重新开始
        return State()
    known = {k: data.get(k) for k in State().__dict__}
    return State(**known)


def save(path: str, state: State) -> None:
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(asdict(state), f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # 原子写
