"""S3 · 飞书提醒卡片（带【开 / 不开】按钮）。

用 lark-cli 发 CardKit 2.0 卡片到目标群 / 私聊。

回调机制：机制 A —— 卡片按钮 callback，value 带 {"__claude_cb": true, ...}。
lark-channel-bridge 会把点击 payload 作为 `[card-click] {...}` 消息送回同一个 CC
session，由承接侧（S4）执行预热。本模块只负责把卡发出去。
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Dict, Optional

from .config import Config
from .rules import Decision
from .providers.base import Usage


def build_card(usage: Usage, decision: Decision) -> Dict[str, Any]:
    five = usage.five_hour
    minutes = decision.minutes_to_reset
    minute_txt = f"{minutes:.0f}" if minutes is not None else "?"
    reset_local = five.resets_at.astimezone().strftime("%H:%M")

    summary = f"额度管家：5h 窗口 {minute_txt} 分钟后重置"
    body_md = (
        f"**5h 窗口即将换挡**\n\n"
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


def _button(text: str, btn_type: str, value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": btn_type,
        "behaviors": [{"type": "callback", "value": value}],
    }


def push_card(usage: Usage, decision: Decision, config: Config,
              dry_run: bool = False) -> Optional[str]:
    """发卡。dry_run 时只打印将要发送的卡 JSON，不真发。返回 lark-cli stdout。"""
    card = build_card(usage, decision)
    card_json = json.dumps(card, ensure_ascii=False)

    if dry_run:
        print("[dry-run] 将发送卡片：")
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return None

    target_args = _target_args(config)
    if target_args is None:
        raise NotifyError("config.feishu 未配置 chat_id / user_id，无处可推")

    # 必须以 bot 身份发；user 身份缺 im:message.send_as_user scope
    cmd = ["lark-cli", "im", "+messages-send", "--as", "bot",
           *target_args, "--msg-type", "interactive", "--content", card_json]

    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError) as e:
        raise NotifyError(f"调用 lark-cli 失败: {e}") from e
    if out.returncode != 0:
        raise NotifyError(f"lark-cli 退出码 {out.returncode}: {out.stderr.strip()[:200]}")
    return out.stdout.strip()


def push_receipt(text: str, config: Config, dry_run: bool = False) -> Optional[str]:
    """S4 回执：点「开」预热后往群里回一条纯文本结果（✅ 已开窗 / ❌ 失败）。"""
    if dry_run:
        print(f"[dry-run] 回执：{text}")
        return None
    target_args = _target_args(config)
    if target_args is None:
        raise NotifyError("config.feishu 未配置 chat_id / user_id，无处可回执")
    content = json.dumps({"text": text}, ensure_ascii=False)
    cmd = ["lark-cli", "im", "+messages-send", "--as", "bot",
           *target_args, "--msg-type", "text", "--content", content]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError) as e:
        raise NotifyError(f"调用 lark-cli 失败: {e}") from e
    if out.returncode != 0:
        raise NotifyError(f"lark-cli 退出码 {out.returncode}: {out.stderr.strip()[:200]}")
    return out.stdout.strip()


def _target_args(config: Config):
    if config.feishu.chat_id:
        return ["--chat-id", config.feishu.chat_id]
    if config.feishu.user_id:
        return ["--user-id", config.feishu.user_id]
    return None


class NotifyError(Exception):
    pass
