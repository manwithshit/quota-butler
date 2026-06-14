"""飞书消息：提醒卡（带【开/不开】）· 回执 · 状态卡（群聊查询用）。

用 lark-cli 发 CardKit 2.0 卡片 / 文本到目标群 / 私聊，必须以 bot 身份
（user 身份缺 im:message.send_as_user scope）。

回调机制：机制 A —— 卡片按钮 callback，value 带 {"__claude_cb": true, ...}。
lark-channel-bridge 会把点击 payload 作为 `[card-click] {...}` 消息送回同一个 CC
session，由承接侧（S4 handler）执行预热。本模块只负责把消息发出去。
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .config import Config
from .rules import Decision
from .providers.base import Usage

PROVIDER_LABEL = {"cc": "Claude Code", "codex": "Codex"}


class NotifyError(Exception):
    pass


# ---- 公共：窗口标签 + lark-cli 发送 --------------------------------------

def window_label(window_seconds: Optional[int]) -> str:
    """据窗口时长给个人话标签：5h 窗口 / 7天窗口 / 月度额度。"""
    if not window_seconds:
        return "窗口"
    if window_seconds <= 6 * 3600:
        return f"{round(window_seconds / 3600)}h 窗口"
    if window_seconds < 28 * 86400:
        return f"{round(window_seconds / 86400)}天窗口"
    return "月度额度"


def _target_args(config: Config):
    if config.feishu.chat_id:
        return ["--chat-id", config.feishu.chat_id]
    if config.feishu.user_id:
        return ["--user-id", config.feishu.user_id]
    return None


def _send(msg_type: str, content_json: str, config: Config) -> str:
    """统一的 lark-cli 发送（bot 身份）。三种消息共用，避免逻辑分叉。"""
    target_args = _target_args(config)
    if target_args is None:
        raise NotifyError("config.feishu 未配置 chat_id / user_id，无处可推")
    cmd = ["lark-cli", "im", "+messages-send", "--as", "bot",
           *target_args, "--msg-type", msg_type, "--content", content_json]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError) as e:
        raise NotifyError(f"调用 lark-cli 失败: {e}") from e
    if out.returncode != 0:
        raise NotifyError(f"lark-cli 退出码 {out.returncode}: {out.stderr.strip()[:200]}")
    return out.stdout.strip()


def _button(text: str, btn_type: str, value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": btn_type,
        "behaviors": [{"type": "callback", "value": value}],
    }


# ---- 提醒卡（S3）---------------------------------------------------------

def build_card(usage: Usage, decision: Decision) -> Dict[str, Any]:
    five = usage.five_hour
    minutes = decision.minutes_to_reset
    minute_txt = f"{minutes:.0f}" if minutes is not None else "?"
    reset_local = five.resets_at.astimezone().strftime("%H:%M")
    wl = window_label(five.window_seconds)

    summary = f"额度管家：{wl} {minute_txt} 分钟后重置"
    body_md = (
        f"**{wl}即将换挡**\n\n"
        f"- 已用额度：**{five.utilization:.0f}%**\n"
        f"- 距重置：**{minute_txt} 分钟**（{reset_local} 重置）\n\n"
        f"要现在预热下一个窗口吗？"
    )

    # value 带 __claude_cb 才会回调到当前 CC session
    open_value = {"__claude_cb": True, "action": "warmup",
                  "resets_at": five.resets_at.isoformat()}
    skip_value = {"__claude_cb": True, "action": "skip",
                  "resets_at": five.resets_at.isoformat()}

    return {
        "schema": "2.0",
        "config": {"summary": {"content": summary}},
        "body": {
            "elements": [
                {"tag": "markdown", "content": body_md},
                {
                    "tag": "column_set",
                    "columns": [
                        {"tag": "column", "elements": [_button("🔥 开", "primary", open_value)]},
                        {"tag": "column", "elements": [_button("不开", "default", skip_value)]},
                    ],
                },
            ]
        },
    }


def push_card(usage: Usage, decision: Decision, config: Config,
              dry_run: bool = False) -> Optional[str]:
    """发提醒卡。dry_run 时只打印卡 JSON，不真发。"""
    card = build_card(usage, decision)
    if dry_run:
        print("[dry-run] 将发送卡片：")
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return None
    return _send("interactive", json.dumps(card, ensure_ascii=False), config)


# ---- 回执（S4）-----------------------------------------------------------

def push_receipt(text: str, config: Config, dry_run: bool = False) -> Optional[str]:
    """点「开」预热后往群里回一条纯文本结果（✅ 已开窗 / ❌ 失败）。"""
    if dry_run:
        print(f"[dry-run] 回执：{text}")
        return None
    content = json.dumps({"text": text}, ensure_ascii=False)
    return _send("text", content, config)


# ---- 状态卡（群聊主动查询）----------------------------------------------

StatusResult = Tuple[str, Optional[Usage], Optional[str]]  # (provider, usage, error)


def build_status_card(results: Sequence[StatusResult]) -> Dict[str, Any]:
    """纯展示卡：把各 provider 当前额度列出来。读不到的标原因。"""
    lines: List[str] = ["**📊 额度状态**", ""]
    for name, usage, err in results:
        label = PROVIDER_LABEL.get(name, name)
        if usage is None:
            lines.append(f"- **{label}**：⚠️ {err}")
            continue
        five = usage.five_hour
        reset_local = five.resets_at.astimezone().strftime("%m-%d %H:%M")
        seg = (f"- **{label}** · {window_label(five.window_seconds)}："
               f"已用 **{five.utilization:.0f}%**，{reset_local} 重置")
        if usage.seven_day:
            sd = usage.seven_day
            seg += f"；{window_label(sd.window_seconds)} {sd.utilization:.0f}%"
        lines.append(seg)
    return {
        "schema": "2.0",
        "config": {"summary": {"content": "额度管家：当前状态"}},
        "body": {"elements": [{"tag": "markdown", "content": "\n".join(lines)}]},
    }


def push_status_card(results: Sequence[StatusResult], config: Config,
                     dry_run: bool = False) -> Optional[str]:
    card = build_status_card(results)
    if dry_run:
        print("[dry-run] 将发送状态卡：")
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return None
    return _send("interactive", json.dumps(card, ensure_ascii=False), config)
