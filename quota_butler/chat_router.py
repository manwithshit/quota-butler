"""Poll Feishu group text commands and route them to quota-butler actions.

This is a pragmatic test router for environments where card callback bridge is
offline. It handles text messages in the configured chat and sends normal
Quota Butler cards back to the same chat.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from . import config as config_mod
from . import handler, menu, query, schedule
from . import state as state_mod

DEFAULT_CONFIG = "~/.quota-butler/config.yaml"


@dataclass(frozen=True)
class ChatMessage:
    message_id: str
    content: str
    sender_type: str
    msg_type: str


def route_once(config_path: str, *, page_size: int = 10) -> int:
    cfg = config_mod.load(config_path)
    if not cfg.feishu.chat_id:
        print("[群聊路由] config.feishu.chat_id 为空")
        return 2

    st = state_mod.load(cfg.resolved_state_path)
    messages = _list_messages(cfg.feishu.chat_id, page_size=page_size)
    pending = _pending_user_texts(messages, st.last_chat_message_id)
    if not pending:
        print("[群聊路由] 没有新的用户文字命令")
        return 0

    for msg in pending:
        action = classify_intent(msg.content)
        if action is None:
            st.last_chat_message_id = msg.message_id
            continue
        print(f"[群聊路由] {msg.message_id} -> {action}")
        rc = _dispatch(action, config_path)
        st.last_chat_message_id = msg.message_id
        state_mod.save(cfg.resolved_state_path, st)
        if rc != 0:
            return rc

    state_mod.save(cfg.resolved_state_path, st)
    return 0


def watch(config_path: str, *, interval_seconds: float = 3.0) -> int:
    print(f"[群聊路由] watch started, interval={interval_seconds}s")
    while True:
        route_once(config_path)
        time.sleep(interval_seconds)


def classify_intent(text: str) -> Optional[str]:
    t = (text or "").strip().lower()
    if not t:
        return None
    if any(k in t for k in ("菜单", "帮助", "help")):
        return "menu"
    if any(k in t for k in ("额度", "状态", "quota", "status")):
        return "query"
    if "查看计划" in t or "当前计划" in t:
        return "plan:view"
    if "取消计划" in t:
        return "plan:cancel"
    if "帮我安排明天" in t or "明天" in t or "tomorrow" in t:
        return "schedule:帮我安排明天"
    if "今天冲刺" in t or "冲刺" in t:
        return "schedule:今天冲刺"
    if "不断粮" in t:
        return "schedule:不断粮模式"
    if "节省" in t:
        return "schedule:节省模式"
    if "平衡" in t:
        return "schedule:平衡模式"
    return None


def _dispatch(action: str, config_path: str) -> int:
    if action == "menu":
        return menu.run(config_path)
    if action == "query":
        return query.run(config_path)
    if action.startswith("schedule:"):
        return schedule.run(config_path, intent=action.split(":", 1)[1])
    if action == "plan:view":
        return handler.handle({"action": "view_schedule"}, config_path=config_path)
    if action == "plan:cancel":
        return handler.handle({"action": "cancel_schedule"}, config_path=config_path)
    return 0


def _list_messages(chat_id: str, *, page_size: int) -> List[ChatMessage]:
    out = subprocess.run(
        [
            "lark-cli",
            "im",
            "+chat-messages-list",
            "--as",
            "bot",
            "--chat-id",
            chat_id,
            "--page-size",
            str(page_size),
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or out.stdout.strip())
    raw: Dict[str, object] = json.loads(out.stdout)
    items = ((raw.get("data") or {}).get("messages") or [])  # type: ignore[union-attr]
    return [
        ChatMessage(
            message_id=str(item.get("message_id", "")),
            content=str(item.get("content", "")),
            sender_type=str((item.get("sender") or {}).get("sender_type", "")),
            msg_type=str(item.get("msg_type", "")),
        )
        for item in items
    ]


def _pending_user_texts(messages: List[ChatMessage], last_seen: Optional[str]) -> List[ChatMessage]:
    chronological = list(reversed(messages))
    if last_seen:
        for idx, msg in enumerate(chronological):
            if msg.message_id == last_seen:
                chronological = chronological[idx + 1:]
                break
    else:
        user_texts = [
            msg for msg in chronological
            if msg.msg_type == "text" and msg.sender_type == "user"
        ]
        return user_texts[-1:]
    return [
        msg for msg in chronological
        if msg.msg_type == "text" and msg.sender_type == "user"
    ]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="quota-butler-chat-router")
    p.add_argument("--config", default=DEFAULT_CONFIG)
    p.add_argument("--once", action="store_true")
    p.add_argument("--watch", action="store_true")
    p.add_argument("--interval", type=float, default=3.0)
    args = p.parse_args(argv)
    if args.watch:
        return watch(args.config, interval_seconds=args.interval)
    return route_once(args.config)


if __name__ == "__main__":
    raise SystemExit(main())
