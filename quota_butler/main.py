"""Periodic V3 poller: detect agents, send recovery reminders, ask at 22:00."""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from . import config as config_mod
from . import state as state_mod
from .agent_status import detect_agents
from .notify import (
    NotifyError,
    build_bedtime_card,
    build_recovery_card,
    push_interactive,
)
from .plan_tasks import cancel_plan_tasks

DEFAULT_CONFIG = "~/.quota-butler/config.yaml"
RECOVERY_FRESHNESS = timedelta(hours=2)


def run(
    config_path: str,
    dry_run: bool = False,
    force: bool = False,
    now: datetime = None,
) -> int:
    del force
    cfg = config_mod.load(config_path)
    with state_mod.locked(cfg.resolved_state_path):
        return _run_locked(cfg, dry_run=dry_run, now=now)


def _run_locked(cfg, *, dry_run, now):
    st = state_mod.load(cfg.resolved_state_path)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local_now = now.astimezone()
    _migrate_and_expire(st, now)

    statuses = detect_agents()
    recovered = _newly_recovered(statuses, st, now)
    snoozed = _due_snoozed_recovery(st, now)
    if snoozed and snoozed not in recovered:
        recovered.insert(0, snoozed)
    bedtime_due = (
        local_now.hour == 22
        and st.last_bedtime_prompt_date != local_now.date().isoformat()
    )

    try:
        if bedtime_due:
            provider = recovered[0][0] if recovered else ""
            push_interactive(
                build_bedtime_card(statuses, recovered_provider=provider),
                _notification_config(cfg, st),
                dry_run,
            )
            if not dry_run:
                st.last_bedtime_prompt_date = local_now.date().isoformat()
                _mark_recoveries(st, recovered)
                st.pending_recovery = None
        elif not _is_quiet(local_now) and not cfg.muted:
            for provider, window_key in recovered:
                push_interactive(
                    build_recovery_card(provider, window_key),
                    _notification_config(cfg, st),
                    dry_run,
                )
            if not dry_run:
                _mark_recoveries(st, recovered)
                if snoozed:
                    st.pending_recovery = None
    except NotifyError as exc:
        print(f"[主动提醒失败] {exc}", file=sys.stderr)
        state_mod.save(cfg.resolved_state_path, st)
        return 3

    st.last_run_at = now.isoformat()
    st.agent_statuses = {
        provider: {
            "state": status.state.value,
            "detail": status.detail[:200],
        }
        for provider, status in statuses.items()
    }
    snapshots = {}
    for provider, status in statuses.items():
        if not status.usage:
            continue
        five = status.usage.five_hour
        snapshots[provider] = {
            "utilization": five.utilization,
            "reset_at": five.resets_at.isoformat() if five.resets_at else None,
        }
    st.provider_snapshots = snapshots
    state_mod.save(cfg.resolved_state_path, st)
    return 0


def _newly_recovered(statuses, st, now):
    previous = st.provider_snapshots or {}
    notified = st.last_recovery_notified_windows or {}
    results = []
    if _active_plan_covers(st.active_plan, now):
        return results
    for provider, status in statuses.items():
        if not status.schedulable or not status.usage:
            continue
        before = previous.get(provider) or {}
        reset_text = before.get("reset_at")
        if not reset_text:
            continue
        try:
            reset_at = datetime.fromisoformat(str(reset_text))
        except ValueError:
            continue
        if reset_at.tzinfo is None:
            reset_at = reset_at.replace(tzinfo=now.tzinfo)
        age = now.astimezone(reset_at.tzinfo) - reset_at
        if age < timedelta(0) or age > RECOVERY_FRESHNESS:
            continue
        if _is_quiet(reset_at.astimezone()):
            continue
        if status.usage.five_hour.utilization > 5:
            continue
        window_key = f"{provider}:{reset_at.isoformat()}"
        if notified.get(provider) == window_key:
            continue
        results.append((provider, window_key))
    return results


def _mark_recoveries(st, recovered):
    notified = dict(st.last_recovery_notified_windows or {})
    for provider, window_key in recovered:
        notified[provider] = window_key
    st.last_recovery_notified_windows = notified


def _notification_config(cfg, st):
    if cfg.feishu.message_id or cfg.feishu.chat_id or cfg.feishu.user_id:
        return cfg
    target = st.notification_target or {}
    if target.get("chat_type") != "p2p":
        return cfg
    chat_id = str(target.get("chat_id") or "").strip()
    if not chat_id:
        return cfg
    return replace(
        cfg,
        feishu=replace(cfg.feishu, chat_id=chat_id, user_id="", message_id=""),
    )


def _due_snoozed_recovery(st, now):
    pending = st.pending_recovery or {}
    provider = str(pending.get("provider") or "")
    window_key = str(pending.get("window_key") or "")
    due_text = str(pending.get("due_at") or "")
    if not provider or not window_key or not due_text:
        return None
    try:
        due_at = datetime.fromisoformat(due_text)
    except ValueError:
        st.pending_recovery = None
        return None
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=now.tzinfo)
    if now.astimezone(due_at.tzinfo) < due_at:
        return None
    if _is_quiet(now.astimezone()):
        st.pending_recovery = None
        return None
    return provider, window_key


def _is_quiet(local_now):
    return local_now.hour >= 23 or local_now.hour < 8


def _active_plan_covers(active, now):
    if not active or active.get("status") != "active":
        return False
    try:
        start = datetime.fromisoformat(str(active.get("work_start")))
        end = datetime.fromisoformat(str(active.get("work_end")))
    except ValueError:
        return False
    if start.tzinfo is None:
        start = start.replace(tzinfo=now.astimezone().tzinfo)
    if end.tzinfo is None:
        end = end.replace(tzinfo=now.astimezone().tzinfo)
    return start <= now.astimezone(start.tzinfo) <= end


def _migrate_and_expire(st, now):
    active = st.active_plan
    if not active:
        return
    version = int(active.get("plan_version") or 0)
    expired = False
    try:
        work_end = datetime.fromisoformat(str(active.get("work_end")))
        if work_end.tzinfo is None:
            work_end = work_end.replace(tzinfo=now.astimezone().tzinfo)
        expired = work_end <= now.astimezone(work_end.tzinfo)
    except ValueError:
        expired = False
    if version < 3 or expired:
        cancel_plan_tasks(active.get("tasks") or [])
        st.active_plan = None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="quota-butler")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    return run(args.config, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
