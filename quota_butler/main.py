"""入口：感知 → 判断 → 推送。一次性跑完即退（launchd 定时拉起）。

用法：
  python -m quota_butler.main                 正常一轮
  python -m quota_butler.main --dry-run       不真发飞书，只打印决策与卡片
  python -m quota_butler.main --config PATH    指定配置文件
  python -m quota_butler.main --force          忽略阈值，强制当作命中（联调用）
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from . import config as config_mod
from . import state as state_mod
from .notify import push_card, NotifyError
from .providers import get_provider
from .providers.base import ProviderError
from .rules import Decision, should_notify

DEFAULT_CONFIG = "~/.quota-butler/config.yaml"


def run(config_path: str, dry_run: bool = False, force: bool = False) -> int:
    cfg = config_mod.load(config_path)
    st = state_mod.load(cfg.resolved_state_path)
    now = datetime.now(timezone.utc)

    # ① 感知（MVP1 感知层固定 CC；warmup_provider 只用于 S4 预热）
    provider = get_provider("cc")
    try:
        usage = provider.read_usage()
    except ProviderError as e:
        print(f"[感知失败] {e}", file=sys.stderr)
        return 2

    five = usage.five_hour
    print(f"[感知] {usage.provider} 5h 利用率={five.utilization:.0f}% "
          f"resets_at={five.resets_at.isoformat()}")

    # ② 判断
    decision = should_notify(usage, st, cfg, now=now)
    if force and not decision.notify:
        decision = Decision(True, "强制触发（--force）", decision.minutes_to_reset)
    print(f"[判断] notify={decision.notify} · {decision.reason}")

    # 更新基础状态
    st.last_run_at = now.isoformat()
    st.last_utilization = five.utilization
    st.last_reset_at = five.resets_at.isoformat()

    # ③ 推送（静音 / 安静时段 都跳过，但仍写状态）
    if decision.notify and cfg.muted:
        print("[推送] 已静音（muted），跳过")
    elif decision.notify and cfg.is_quiet(now):
        print("[推送] 安静时段，跳过")
    elif decision.notify:
        try:
            push_card(usage, decision, cfg, dry_run=dry_run)
            if not dry_run:
                st.last_notified_reset_at = five.resets_at.isoformat()
                print("[推送] 已发飞书卡片")
        except NotifyError as e:
            print(f"[推送失败] {e}", file=sys.stderr)
            state_mod.save(cfg.resolved_state_path, st)
            return 3

    state_mod.save(cfg.resolved_state_path, st)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="quota-butler")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    return run(args.config, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
