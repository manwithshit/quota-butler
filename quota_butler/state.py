"""本地状态文件（JSON）读写。用于去重 / 防重复打扰。不上 SQLite。

字段：
  last_run_at            上次运行时间（ISO8601）
  last_utilization       上次读到的 5h 利用率
  last_reset_at          上次读到的 5h resets_at
  last_notified_reset_at 上次「已推送提醒」对应的 resets_at —— 推送去重的关键
  last_warmed_reset_at   上次「已点开预热」对应的 resets_at —— 防重复预热
  last_action            上次回调动作（warmup / skip），便于日志追溯
  last_chat_message_id   上次离线诊断路由处理到的 message_id
  notification_target    主动提醒目标；只允许记录独立机器人私聊
  pending_warmup_receipts 免打扰时段内完成/失败的预热结果，离开免打扰后补发
"""

from __future__ import annotations

import json
import os
import fcntl
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass
class State:
    schema_version: int = 3
    last_run_at: Optional[str] = None
    last_action: Optional[str] = None
    last_chat_message_id: Optional[str] = None
    active_plan: Optional[Dict[str, Any]] = None
    provider_snapshots: Optional[Dict[str, Dict[str, Any]]] = None
    muted_until: Optional[str] = None
    agent_statuses: Optional[Dict[str, Dict[str, Any]]] = None
    last_warmed_windows: Optional[Dict[str, str]] = None
    last_bedtime_prompt_date: Optional[str] = None
    proposed_plan_id: Optional[str] = None
    last_recovery_notified_windows: Optional[Dict[str, str]] = None
    pending_recovery: Optional[Dict[str, str]] = None
    notification_target: Optional[Dict[str, str]] = None
    pending_warmup_receipts: Optional[list] = None


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
    defaults = asdict(State())
    known = {key: data.get(key, default) for key, default in defaults.items()}
    if known["schema_version"] is None:
        known["schema_version"] = defaults["schema_version"]
    return State(**known)


def save(path: str, state: State) -> None:
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(asdict(state), f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # 原子写


@contextmanager
def locked(path: str):
    """Serialize full read-modify-write transactions across poller/callback jobs."""
    lock_path = os.path.expanduser(path) + ".lock"
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
