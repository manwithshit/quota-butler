"""V2 调度入口：根据用户工作画像生成 AI Agent 接力计划。

集成契约（给 bridge / 承接 agent）：

    用户说「帮我安排明天」→ python3 -m quota_butler.schedule --intent tomorrow
    用户说「不断粮模式」  → python3 -m quota_butler.schedule --intent 不断粮模式
    用户说「今天冲刺」    → python3 -m quota_butler.schedule --intent 今天冲刺

本模块只生成并推送计划卡，不直接执行预热。真正采用计划后的定时执行可以在后续
版本接入 launchd/automation；当前版本先把调度建议闭环跑通。
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta

from . import config as config_mod
from .notify import NotifyError, push_schedule_card
from .planner import plan_from_config

DEFAULT_CONFIG = "~/.quota-butler/config.yaml"


def run(config_path: str, *, intent: str = "", dry_run: bool = False) -> int:
    cfg = config_mod.load(config_path)
    target_date = _target_date(intent)
    try:
        plan = plan_from_config(cfg, intent=intent or None, target_date=target_date)
    except ValueError as e:
        print(f"[调度] 生成计划失败：{e}")
        return 2

    print(
        f"[调度] {plan.mode} · agents={','.join(plan.agents)} · "
        f"CAS={plan.cas * 100:.0f}% · waiting={plan.waiting_minutes:.0f}min"
    )
    try:
        push_schedule_card(plan, cfg, dry_run=dry_run)
    except NotifyError as e:
        print(f"[调度] 发卡失败：{e}")
        return 3
    return 0


def _target_date(intent: str) -> date:
    today = date.today()
    if "明天" in (intent or "") or "tomorrow" in (intent or "").lower():
        return today + timedelta(days=1)
    return today


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="quota-butler-schedule")
    p.add_argument("--config", default=DEFAULT_CONFIG)
    p.add_argument("--intent", default="")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    return run(args.config, intent=args.intent, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
