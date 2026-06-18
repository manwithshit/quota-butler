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
from .notify import NotifyError, push_card, push_oneup_card
from .oneup import should_offer_oneup
from .plan_tasks import cancel_plan_tasks
from .planner import SUPPORTED_AGENTS, parse_agents
from .providers import get_provider
from .providers.base import ProviderError
from .rules import Decision, should_notify

DEFAULT_CONFIG = "~/.quota-butler/config.yaml"


def run(
    config_path: str,
    dry_run: bool = False,
    force: bool = False,
    now: datetime = None,
) -> int:
    cfg = config_mod.load(config_path)
    st = state_mod.load(cfg.resolved_state_path)
    now = now or datetime.now(timezone.utc)
    if _expire_active_plan(st, now):
        state_mod.save(cfg.resolved_state_path, st)

    if cfg.sense_provider not in SUPPORTED_AGENTS:
        print(f"[感知失败] 未知 provider: {cfg.sense_provider}", file=sys.stderr)
        return 2

    usage_errors = {}
    try:
        scheduler_agents = parse_agents(cfg.scheduler_agents)
    except ValueError as e:
        print(f"[调度感知失败] {e}", file=sys.stderr)
        scheduler_agents = (cfg.sense_provider,)
    agent_order = tuple(dict.fromkeys((cfg.sense_provider, *scheduler_agents)))
    usages = {}
    for name in agent_order:
        try:
            usages[name] = get_provider(name).read_usage()
        except (ProviderError, NotImplementedError) as e:
            usage_errors[name] = str(e)
            print(f"[感知] {name} 读取失败：{e}")

    if not usages:
        detail = usage_errors.get(cfg.sense_provider, "所有 Agent 均读取失败")
        print(f"[感知失败] {detail}", file=sys.stderr)
        return 2

    usage = usages.get(cfg.sense_provider)
    if usage is not None:
        five = usage.five_hour
        print(f"[感知] {usage.provider} 5h 利用率={five.utilization:.0f}% "
              f"resets_at={five.resets_at.isoformat() if five.resets_at else 'None'}")
        decision = should_notify(usage, st, cfg, now=now)
        if force and not decision.notify:
            decision = Decision(True, "强制触发（--force）", decision.minutes_to_reset)
    else:
        five = None
        decision = Decision(False, f"{cfg.sense_provider} 读取失败，跳过旧版换挡提醒")
    print(f"[判断] notify={decision.notify} · {decision.reason}")

    oneup = None
    for name in agent_order:
        candidate_usage = usages.get(name)
        if candidate_usage is None:
            continue
        candidate = should_offer_oneup(name, candidate_usage, st, cfg, now=now)
        if candidate.notify:
            oneup = candidate
            break

    # 更新基础状态
    st.last_run_at = now.isoformat()
    if five is not None:
        st.last_utilization = five.utilization
        st.last_reset_at = five.resets_at.isoformat() if five.resets_at else None

    # ③ 推送（静音 / 安静时段 都跳过，但仍写状态）
    regular_notice_handled = decision.notify
    if decision.notify and cfg.muted:
        print("[推送] 已静音（muted），跳过")
    elif decision.notify and cfg.is_quiet(now):
        print("[推送] 安静时段，跳过")
    elif decision.notify:
        try:
            push_card(usage, decision, cfg, dry_run=dry_run)
            if not dry_run and five is not None and five.resets_at:
                st.last_notified_reset_at = five.resets_at.isoformat()
                print("[推送] 已发飞书卡片")
        except NotifyError as e:
            print(f"[推送失败] {e}", file=sys.stderr)
            state_mod.save(cfg.resolved_state_path, st)
            return 3

    if oneup and not regular_notice_handled:
        other_results = [
            (name, usages.get(name), usage_errors.get(name))
            for name in agent_order
            if name != oneup.provider
        ]
        try:
            push_oneup_card(
                oneup.provider or cfg.sense_provider,
                other_results,
                cfg,
                dry_run=dry_run,
                window_key=oneup.window_key or "",
            )
            if not dry_run:
                st.last_oneup_notified_window = oneup.window_key
                st.pending_oneup = None
                print(f"[主动推送] {oneup.provider} 已恢复，已发 one-up 卡")
        except NotifyError as e:
            print(f"[主动推送失败] {e}", file=sys.stderr)
            state_mod.save(cfg.resolved_state_path, st)
            return 3

    if not dry_run:
        snapshots = dict(st.provider_snapshots or {})
        for name, item in usages.items():
            item_five = item.five_hour
            snapshots[name] = {
                "utilization": item_five.utilization,
                "reset_at": (
                    item_five.resets_at.isoformat()
                    if item_five.resets_at else None
                ),
            }
        st.provider_snapshots = snapshots

    state_mod.save(cfg.resolved_state_path, st)
    return 0


def _expire_active_plan(st, now: datetime) -> bool:
    active = st.active_plan
    if not active or active.get("status") != "active":
        return False
    try:
        work_end = datetime.fromisoformat(str(active.get("work_end")))
    except (TypeError, ValueError):
        return False
    if work_end.tzinfo is None:
        work_end = work_end.replace(tzinfo=timezone.utc)
    if work_end > now:
        return False
    cancel_plan_tasks(active.get("tasks") or [])
    st.active_plan = None
    print(f"[计划] {active.get('plan_id', '')} 已过期，已清理")
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="quota-butler")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    return run(args.config, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
