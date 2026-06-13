"""配置加载。

读 config.yaml（与 config.example.yaml 同结构）。优先用 PyYAML；未安装时回退到
一个只支持本项目所需子集的极简解析器，保证零第三方依赖也能开箱即跑。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


# ---- 默认值（config 未给时的兜底）---------------------------------------

DEFAULTS: Dict[str, Any] = {
    "interval_min": 5,         # launchd 拉起间隔（仅文档用途，实际间隔在 plist）
    "reset_soon_min": 20,      # 距 reset 小于这个分钟数 → 触发
    "waste_pct": None,         # 可选叠加：利用率低于此值才提醒（None = 关）
    "warmup_provider": "cc",   # 预热用哪个 provider：cc / codex
    "warmup_prompt": "say hi", # 预热消息（越短越省）
    "muted": False,            # 静音开关：True 时只判断不推送
    "state_path": "~/.quota-butler/state.json",
}


@dataclass
class FeishuConfig:
    chat_id: str = ""          # 目标群 / 会话 chat_id（oc_...）
    user_id: str = ""          # 可选：私聊目标 ou_...


@dataclass
class Config:
    interval_min: int = DEFAULTS["interval_min"]
    reset_soon_min: int = DEFAULTS["reset_soon_min"]
    waste_pct: Optional[float] = DEFAULTS["waste_pct"]
    warmup_provider: str = DEFAULTS["warmup_provider"]
    warmup_prompt: str = DEFAULTS["warmup_prompt"]
    muted: bool = DEFAULTS["muted"]
    state_path: str = DEFAULTS["state_path"]
    feishu: FeishuConfig = field(default_factory=FeishuConfig)

    @property
    def resolved_state_path(self) -> str:
        return os.path.expanduser(self.state_path)


def _coerce(cfg: Config) -> None:
    """把可能是字符串的字段强转成正确类型（fallback 解析器需要）。"""
    cfg.interval_min = int(cfg.interval_min)
    cfg.reset_soon_min = int(cfg.reset_soon_min)
    if cfg.waste_pct is not None and cfg.waste_pct != "":
        cfg.waste_pct = float(cfg.waste_pct)
    else:
        cfg.waste_pct = None
    if isinstance(cfg.muted, str):
        cfg.muted = cfg.muted.strip().lower() in ("true", "1", "yes", "on")


def from_dict(data: Dict[str, Any]) -> Config:
    data = dict(data or {})
    feishu_raw = data.pop("feishu", {}) or {}
    cfg = Config(**{k: v for k, v in data.items() if k in DEFAULTS})
    cfg.feishu = FeishuConfig(
        chat_id=str(feishu_raw.get("chat_id", "")),
        user_id=str(feishu_raw.get("user_id", "")),
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
