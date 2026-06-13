"""S4 · 卡片回调处理器（承接「开 / 不开」点击）。

## 它在闭环里的位置

quota-butler 的感知端（main.py）是 launchd 定时拉起、跑完即退的进程，**不常驻**，
所以按钮点击不可能落回那个已退出的进程。回调走的是 lark-channel-bridge 这条反向链路：

    用户点【🔥 开】
      → 飞书 → lark-channel-bridge
      → bridge 把 `[card-click] {action, resets_at, ...}` 作为消息送进一个 CC session
      → 承接侧执行：python -m quota_butler.handler '<payload-json>'
      → 本处理器：action=warmup → 调 provider.warmup() → 发回执到群
                  action=skip   → 静默写状态

这样「点开 → 预热 → 回执」是确定性的、可复现、可截图，不依赖 agent 自由发挥。

## 集成契约（给 bridge / 承接 agent）

当收到 `[card-click] {...}`（bridge 已去掉 __claude_cb marker）时，把那段 JSON 原样
作为唯一参数调用：

    python3 -m quota_butler.handler '<那段 JSON>'

也可从 stdin 读：

    echo '<那段 JSON>' | python3 -m quota_butler.handler

## 防重复

同一个 resets_at 窗口若已预热过（state.last_warmed_reset_at 命中），再点「开」只回一句
「该窗口已开过」，不重复烧 token。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from . import config as config_mod
from . import state as state_mod
from .notify import push_receipt, NotifyError
from .providers import get_provider
from .providers.base import ProviderError
from .window import same_window

DEFAULT_CONFIG = "~/.quota-butler/config.yaml"


def handle(payload: dict, config_path: str = DEFAULT_CONFIG,
           dry_run: bool = False) -> int:
    cfg = config_mod.load(config_path)
    st = state_mod.load(cfg.resolved_state_path)
    action = (payload or {}).get("action", "")
    resets_at = (payload or {}).get("resets_at")

    st.last_action = action
    st.last_run_at = datetime.now(timezone.utc).isoformat()

    if action == "skip":
        print("[回调] 用户点了「不开」，静默")
        state_mod.save(cfg.resolved_state_path, st)
        return 0

    if action != "warmup":
        print(f"[回调] 未知 action={action!r}，忽略", file=sys.stderr)
        state_mod.save(cfg.resolved_state_path, st)
        return 1

    # 防重复预热：同窗口已开过就不再烧 token（容差比较，resets_at 微秒会漂移）
    if resets_at and same_window(st.last_warmed_reset_at, resets_at):
        msg = "ℹ️ 该窗口已经开过了，不重复预热"
        print(f"[回调] {msg}")
        _safe_receipt(msg, cfg, dry_run)
        state_mod.save(cfg.resolved_state_path, st)
        return 0

    # ⚠️ 6/15 起 CC `claude -p` 独立计费——这是产品已知并接受的选择
    print(f"[回调] 点「开」→ 用 {cfg.warmup_provider} 预热：{cfg.warmup_prompt!r}")
    if dry_run:
        # dry-run 绝不真烧 token：只模拟，不调 warmup()
        print("[dry-run] 跳过真实 claude -p 调用")
        _safe_receipt("✅ 已开窗，新窗口从现在起算", cfg, dry_run)
        print("[回调] 预热完成（dry-run 模拟），已回执")
        return 0
    provider = get_provider(cfg.warmup_provider)
    try:
        provider.warmup(cfg.warmup_prompt)
    except ProviderError as e:
        err = f"❌ 开窗失败：{e}"
        print(f"[回调] {err}", file=sys.stderr)
        _safe_receipt(err, cfg, dry_run)
        state_mod.save(cfg.resolved_state_path, st)
        return 3

    st.last_warmed_reset_at = resets_at
    state_mod.save(cfg.resolved_state_path, st)
    _safe_receipt("✅ 已开窗，新窗口从现在起算", cfg, dry_run)
    print("[回调] 预热完成，已回执")
    return 0


def _safe_receipt(text: str, cfg, dry_run: bool) -> None:
    """回执失败不应让整体退非零——预热本身已成功，回执是锦上添花。"""
    try:
        push_receipt(text, cfg, dry_run=dry_run)
    except NotifyError as e:
        print(f"[回调] 回执发送失败（不影响预热结果）：{e}", file=sys.stderr)


def _read_payload(argv) -> dict:
    raw = argv[1] if len(argv) > 1 else sys.stdin.read()
    raw = raw.strip()
    if not raw:
        raise ValueError("未提供 payload（参数或 stdin 均为空）")
    return json.loads(raw)


def main(argv=None) -> int:
    argv = sys.argv if argv is None else argv
    dry_run = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]
    try:
        payload = _read_payload(argv)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[回调] payload 解析失败：{e}", file=sys.stderr)
        return 2
    return handle(payload, dry_run=dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
