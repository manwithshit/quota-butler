"""Handle verified Feishu card callbacks for Quota Butler V3."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Mapping

from . import config as config_mod
from . import state as state_mod
from .agent_status import detect_agents
from .notify import (
    NotifyError,
    build_agent_control_card,
    build_time_card,
    build_time_mode_card,
    push_active_plan_card,
    push_interactive,
    push_receipt,
    push_schedule_card,
    push_status_card,
)
from .plan_tasks import (
    PlanTaskError,
    cancel_plan_tasks,
    install_plan_tasks,
    validate_plan_record,
)
from .planner import build_plan
from .providers import get_provider
from .providers.base import ProviderError
from .schedule_flow import (
    FLOW_VERSION,
    PlanRequest,
    parse_plan_request,
    validate_flow_context,
)

DEFAULT_CONFIG = "~/.quota-butler/config.yaml"


def handle(
    payload: dict,
    config_path: str = DEFAULT_CONFIG,
    dry_run: bool = False,
) -> int:
    cfg = config_mod.load(config_path)
    with state_mod.locked(cfg.resolved_state_path):
        return _handle_locked(payload, cfg, config_path, dry_run)


def _handle_locked(payload, cfg, config_path, dry_run):
    st = state_mod.load(cfg.resolved_state_path)
    _migrate_v2(st)
    action = str((payload or {}).get("action") or "")
    st.last_action = action
    st.last_run_at = datetime.now(timezone.utc).isoformat()

    try:
        if action == "query_status":
            statuses = detect_agents()
            push_status_card(statuses, cfg, dry_run=dry_run)
            st.agent_statuses = _status_snapshot(statuses)
            return _finish(cfg, st, 0)

        if action == "schedule_intent":
            target = _target_date(payload)
            push_interactive(build_time_mode_card(target), cfg, dry_run)
            return _finish(cfg, st, 0)

        if action == "schedule_flow":
            return _handle_schedule_flow(payload, cfg, st, dry_run)

        if action in ("adjust_schedule_agents", "redetect_agents"):
            request = _request_from_payload(payload, available_count=1)
            statuses = detect_agents()
            push_interactive(
                build_agent_control_card(request, statuses),
                cfg,
                dry_run,
            )
            st.agent_statuses = _status_snapshot(statuses)
            return _finish(cfg, st, 0)

        if action == "adjust_schedule_time":
            request = _request_from_payload(payload, available_count=1)
            push_interactive(build_time_card(request), cfg, dry_run)
            return _finish(cfg, st, 0)

        if action == "adopt_schedule":
            return _adopt_schedule(payload, cfg, st, config_path, dry_run)

        if action == "view_schedule":
            if not st.active_plan:
                _safe_receipt("当前没有生效计划", cfg, dry_run)
            else:
                push_active_plan_card(st.active_plan, cfg, dry_run=dry_run)
            return _finish(cfg, st, 0)

        if action == "cancel_schedule":
            if st.active_plan and not dry_run:
                cancel_plan_tasks(st.active_plan.get("tasks") or [])
                st.active_plan = None
            _safe_receipt("✅ 已取消计划，未执行任务已删除", cfg, dry_run)
            return _finish(cfg, st, 0)

        if action in ("warmup_now", "scheduled_warmup"):
            return _warmup(payload, cfg, st, dry_run)

        if action == "recovery_snooze":
            minutes = max(1, min(int(payload.get("minutes") or 30), 24 * 60))
            due_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
            st.pending_recovery = {
                "provider": str(payload.get("provider") or ""),
                "window_key": str(payload.get("window_key") or ""),
                "due_at": due_at.isoformat(),
            }
            _safe_receipt(f"好，{minutes} 分钟后再提醒", cfg, dry_run)
            return _finish(cfg, st, 0)

        if action in ("recovery_skip", "tomorrow_skip", "schedule_remind_only", "skip"):
            if action == "recovery_skip":
                st.pending_recovery = None
            if action == "schedule_remind_only":
                _safe_receipt("已改为仅提醒，不会创建本地预热任务", cfg, dry_run)
            return _finish(cfg, st, 0)

        _safe_receipt("该卡片已失效，请重新打开菜单", cfg, dry_run)
        return _finish(cfg, st, 1)
    except NotifyError as exc:
        print(f"[飞书发送失败] {exc}", file=sys.stderr)
        return _finish(cfg, st, 3)


def _handle_schedule_flow(payload, cfg, st, dry_run):
    try:
        validate_flow_context(payload)
    except ValueError as exc:
        _safe_receipt(str(exc), cfg, dry_run)
        return _finish(cfg, st, 4)
    step = str(payload.get("step") or "")
    request_raw = dict(payload.get("request") or {})
    request_raw.setdefault("target_date", payload.get("target_date"))

    if step in ("edit_time_point", "edit_time_range"):
        request_raw["time_mode"] = "point" if step.endswith("point") else "range"
        try:
            request = parse_plan_request(request_raw, available_agent_count=1)
        except ValueError:
            request = PlanRequest(
                date.fromisoformat(str(payload["target_date"])),
                "point" if step.endswith("point") else "range",
                "09:00",
                "14:00" if step.endswith("point") else "17:00",
                "auto",
            )
        push_interactive(build_time_card(request), cfg, dry_run)
        return _finish(cfg, st, 0)

    if step != "generate_plan":
        _safe_receipt("该卡片已失效，请重新打开菜单", cfg, dry_run)
        return _finish(cfg, st, 4)

    form = payload.get("form_value")
    if isinstance(form, Mapping):
        for key in ("work_start", "work_end"):
            if key in form:
                request_raw[key] = form[key]
    if payload.get("agent_strategy"):
        request_raw["agent_strategy"] = payload["agent_strategy"]
    statuses = detect_agents()
    usages = {
        provider: status.usage
        for provider, status in statuses.items()
        if status.schedulable and status.usage is not None
    }
    st.agent_statuses = _status_snapshot(statuses)
    if not usages:
        _safe_receipt("暂时没有可用于规划的 Agent", cfg, dry_run)
        return _finish(cfg, st, 4)
    try:
        request = parse_plan_request(
            request_raw,
            available_agent_count=len(usages),
        )
        plan = build_plan(request, usages)
    except ValueError as exc:
        fallback = PlanRequest(
            date.fromisoformat(str(request_raw["target_date"])),
            str(request_raw.get("time_mode") or "point"),
            str(request_raw.get("work_start") or "09:00").split()[0],
            str(request_raw.get("work_end") or "14:00").split()[0],
            str(request_raw.get("agent_strategy") or "auto"),
        )
        push_interactive(build_time_card(fallback, error=str(exc)), cfg, dry_run)
        return _finish(cfg, st, 4)
    push_schedule_card(plan, cfg, dry_run=dry_run)
    st.proposed_plan_id = _plan_id_from_push(plan)
    return _finish(cfg, st, 0)


def _adopt_schedule(payload, cfg, st, config_path, dry_run):
    candidate = payload.get("plan")
    if not isinstance(candidate, dict) or candidate.get("plan_version") != 3:
        _safe_receipt("该计划已失效，请重新规划", cfg, dry_run)
        return _finish(cfg, st, 4)
    if (
        st.proposed_plan_id
        and str(candidate.get("plan_id") or "") != st.proposed_plan_id
    ):
        _safe_receipt("该计划不是最新预览，请采用最新计划", cfg, dry_run)
        return _finish(cfg, st, 4)
    if st.active_plan and st.active_plan.get("status") == "active":
        _safe_receipt("已有生效计划，请先取消后再采用新计划", cfg, dry_run)
        return _finish(cfg, st, 4)
    try:
        record = validate_plan_record(candidate)
    except PlanTaskError as exc:
        _safe_receipt(f"❌ 计划不可采用：{exc}", cfg, dry_run)
        return _finish(cfg, st, 4)
    statuses = detect_agents(tuple(record.get("agents") or ()))
    unavailable = [
        provider
        for provider in record.get("agents") or []
        if provider not in statuses or not statuses[provider].schedulable
    ]
    if unavailable:
        labels = "、".join(unavailable)
        _safe_receipt(f"❌ Agent 状态已变化，请重新生成计划：{labels}", cfg, dry_run)
        return _finish(cfg, st, 4)
    try:
        tasks = (
            []
            if dry_run
            else install_plan_tasks(record, cfg, config_path=config_path)
        )
    except PlanTaskError as exc:
        _safe_receipt(f"❌ 采用计划失败：{exc}", cfg, dry_run)
        return _finish(cfg, st, 3)
    if not dry_run:
        record["status"] = "active"
        record["adopted_at"] = datetime.now(timezone.utc).isoformat()
        record["tasks"] = tasks
        st.active_plan = record
        st.proposed_plan_id = None
    _safe_receipt(f"✅ 已采用计划，已创建 {len(tasks)} 个预热任务", cfg, dry_run)
    return _finish(cfg, st, 0)


def _warmup(payload, cfg, st, dry_run):
    provider_name = str(payload.get("provider") or "")
    if provider_name not in ("cc", "codex"):
        _safe_receipt("预热 Agent 无效", cfg, dry_run)
        return _finish(cfg, st, 4)
    task = None
    if payload.get("action") == "scheduled_warmup":
        active = st.active_plan or {}
        if active.get("plan_id") != payload.get("plan_id"):
            return _finish(cfg, st, 4)
        for item in active.get("tasks") or []:
            if (
                item.get("provider") == provider_name
                and item.get("scheduled_for") == payload.get("scheduled_for")
                and item.get("status") == "pending"
            ):
                task = item
                break
        if task is None:
            return _finish(cfg, st, 4)
    window_key = str(payload.get("window_key") or "")
    warmed = dict(st.last_warmed_windows or {})
    if window_key and warmed.get(provider_name) == window_key:
        _safe_receipt("这个窗口已经预热过了", cfg, dry_run)
        return _finish(cfg, st, 0)
    try:
        if not dry_run:
            get_provider(provider_name).warmup(cfg.warmup_prompt)
    except (ProviderError, NotImplementedError) as exc:
        if task is not None:
            task["status"] = "failed"
            task["error"] = str(exc)[:200]
        _safe_receipt(f"❌ {provider_name} 预热失败：{exc}", cfg, dry_run)
        return _finish(cfg, st, 3)
    if task is not None:
        task["status"] = "executed"
        task["executed_at"] = datetime.now(timezone.utc).isoformat()
        cancel_plan_tasks([task])
    if window_key:
        warmed[provider_name] = window_key
        st.last_warmed_windows = warmed
    _safe_receipt(
        f"✅ {provider_name} 已预热，新的额度窗口已开始",
        cfg,
        dry_run,
    )
    return _finish(cfg, st, 0)


def _request_from_payload(payload: Mapping[str, Any], available_count: int):
    raw = dict(payload.get("request") or {})
    return parse_plan_request(raw, available_agent_count=available_count)


def _target_date(payload):
    raw = str(payload.get("target_date") or "")
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    return date.today() + timedelta(days=1)


def _status_snapshot(statuses):
    return {
        provider: {
            "state": status.state.value,
            "detail": status.detail[:200],
            "utilization": (
                status.usage.five_hour.utilization if status.usage else None
            ),
            "reset_at": (
                status.usage.five_hour.resets_at.isoformat()
                if status.usage and status.usage.five_hour.resets_at
                else None
            ),
        }
        for provider, status in statuses.items()
    }


def _migrate_v2(st):
    active = st.active_plan
    if active and int(active.get("plan_version") or 0) < 3:
        cancel_plan_tasks(active.get("tasks") or [])
        st.active_plan = None


def _safe_receipt(text, cfg, dry_run):
    try:
        push_receipt(text, cfg, dry_run=dry_run)
    except NotifyError as exc:
        print(f"[回执失败] {exc}", file=sys.stderr)


def _finish(cfg, st, code):
    state_mod.save(cfg.resolved_state_path, st)
    return code


def _plan_id_from_push(plan):
    from .plan_tasks import plan_record
    return plan_record(plan)["plan_id"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="quota-butler-handler")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("payload", nargs="?")
    args = parser.parse_args(argv)
    if args.payload:
        payload = json.loads(args.payload)
    else:
        payload = json.load(sys.stdin)
    return handle(payload, config_path=args.config)


if __name__ == "__main__":
    raise SystemExit(main())
