"""配置加载。

读 config.yaml（与 config.example.yaml 同结构）。优先用 PyYAML；未安装时回退到
一个只支持本项目所需子集的极简解析器，保证零第三方依赖也能开箱即跑。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from typing import Any, Dict, Optional


# ---- 默认值（config 未给时的兜底）---------------------------------------

DEFAULTS: Dict[str, Any] = {
    "interval_min": 15,
    "warmup_prompt": "say hi",
    "plan_tasks_dir": "~/.quota-butler/plan-tasks",
    "muted": False,
    "state_path": "~/.quota-butler/state.json",
}


@dataclass
class FeishuConfig:
    chat_id: str = ""          # 目标群 / 会话 chat_id（oc_...）
    user_id: str = ""          # 可选：私聊目标 ou_...
    message_id: str = ""       # 可选：回复原始消息 om_...


@dataclass
class QuietHours:
    """安静时段（本地时间 HH:MM）。区间内即使命中也不推送，免半夜打扰。

    start/end 任一为空 = 关闭。支持跨午夜（如 23:00–08:00）。
    """
    start: str = ""
    end: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.start and self.end)

    def contains(self, t: dtime) -> bool:
        if not self.enabled:
            return False
        try:
            s, e = _parse_hhmm(self.start), _parse_hhmm(self.end)
        except (ValueError, TypeError):
            return False
        if s == e:
            return False
        if s < e:
            return s <= t < e
        return t >= s or t < e  # 跨午夜


def _parse_hhmm(value: str) -> dtime:
    hh, _, mm = str(value).strip().partition(":")
    return dtime(int(hh), int(mm or 0))


@dataclass
class Config:
    interval_min: int = DEFAULTS["interval_min"]
    warmup_prompt: str = DEFAULTS["warmup_prompt"]
    plan_tasks_dir: str = DEFAULTS["plan_tasks_dir"]
    muted: bool = DEFAULTS["muted"]
    state_path: str = DEFAULTS["state_path"]
    feishu: FeishuConfig = field(default_factory=FeishuConfig)
    quiet_hours: QuietHours = field(default_factory=QuietHours)

    @property
    def resolved_state_path(self) -> str:
        return os.path.expanduser(self.state_path)

    def is_quiet(self, now: datetime) -> bool:
        """now: aware datetime（UTC）。转本地时间判断是否处于安静时段。"""
        return self.quiet_hours.contains(now.astimezone().time())


def _coerce(cfg: Config) -> None:
    """把可能是字符串的字段强转成正确类型（fallback 解析器需要）。"""
    cfg.interval_min = int(cfg.interval_min)
    if isinstance(cfg.muted, str):
        cfg.muted = cfg.muted.strip().lower() in ("true", "1", "yes", "on")


def from_dict(data: Dict[str, Any]) -> Config:
    data = dict(data or {})
    feishu_raw = data.pop("feishu", {}) or {}
    quiet_raw = data.pop("quiet_hours", {}) or {}
    cfg = Config(**{k: v for k, v in data.items() if k in DEFAULTS})
    cfg.feishu = FeishuConfig(
        chat_id=str(feishu_raw.get("chat_id", "")),
        user_id=str(feishu_raw.get("user_id", "")),
        message_id=str(feishu_raw.get("message_id", "")),
    )
    cfg.quiet_hours = QuietHours(
        start=str(quiet_raw.get("start", "") or ""),
        end=str(quiet_raw.get("end", "") or ""),
    )
    _coerce(cfg)
    return cfg


def load(path: str) -> Config:
    """从 yaml 文件加载配置；文件不存在时返回默认配置。"""
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        return from_dict({})
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return from_dict(_parse_yaml(text))


# ---- YAML 解析：优先 PyYAML，回退极简实现 --------------------------------

def _parse_yaml(text: str) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore

        return yaml.safe_load(text) or {}
    except ImportError:
        return _tiny_yaml(text)


def _tiny_yaml(text: str) -> Dict[str, Any]:
    """只支持本项目用到的子集：顶层 key: value + 单层嵌套 mapping（feishu:）。

    够用即可，不追求通用。装了 PyYAML 就不会走到这里。
    """
    root: Dict[str, Any] = {}
    current_section: Optional[Dict[str, Any]] = None

    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        key, _, value = line.strip().partition(":")
        key = key.strip()
        value = value.strip()

        if indent == 0:
            if value == "":  # 开了一个 section
                current_section = {}
                root[key] = current_section
            else:
                root[key] = _scalar(value)
                current_section = None
        else:
            if current_section is None:
                continue
            current_section[key] = _scalar(value)
    return root


def _scalar(value: str) -> Any:
    if value == "" or value.lower() in ("null", "~", "none"):
        return None
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    if value and value[0] in "\"'" and value[-1] == value[0]:
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value
