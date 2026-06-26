"""Handle verified Feishu card callbacks for Quota Butler V3."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Mapping

from . import config as config_mod
from . import state as state_mod
from .agent_status import detect_agents
from .notify import (
    NotifyError,
    PROVIDER_LABEL,
    build_agent_control_card,
    build_manual_warmup_card,
    build_time_card,
    push_active_plan_card,
    push_command_menu_card,
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
    reply_cfg = _reply_config(cfg, payload)
    st = state_mod.load(cfg.resolved_state_path)
    _migrate_v2(st)
    _remember_notification_target(st, payload)
    action = str((payload or {}).get("action") or "")
    st.last_action = action
    st.last_run_at = datetime.now(timezone.utc).isoformat()

    try:
        if action == "query_status":
            statuses = detect_agents()
            push_status_card(statuses, reply_cfg, dry_run=dry_run)
            st.agent_statuses = _status_snapshot(statuses)
            return _finish(cfg, st, 0)

        if action in ("menu", "command_menu"):
            push_command_menu_card(reply_cfg, dry_run=dry_run)
            return _finish(cfg, st, 0)

        if action == "manual_warmup":
            statuses = detect_agents()
            push_interactive(build_manual_warmup_card(statuses), reply_cfg, dry_run)
            st.agent_statuses = _status_snapshot(statuses)
            return _finish(cfg, st, 0)

        if action == "schedule_intent":
            target = _target_date(payload)
            push_interactive(
                build_time_card(PlanRequest(target, "point", "09:00", "14:00", "auto")),
                reply_cfg,
                dry_run,
            )
            return _finish(cfg, st, 0)

        if action == "schedule_flow":
            return _handle_schedule_flow(payload, reply_cfg, st, dry_run)

        if action in ("adjust_schedule_agents", "redetect_agents"):
            request = _request_from_payload(payload, available_count=1)
            statuses = detect_agents()
            push_interactive(
                build_agent_control_card(request, statuses),
                reply_cfg,
                dry_run,
            )
            st.agent_statuses = _status_snapshot(statuses)
            return _finish(cfg, st, 0)

        if action == "adjust_schedule_time":
            request = _request_from_payload(payload, available_count=1)
            push_interactive(build_time_card(request), reply_cfg, dry_run)
            return _finish(cfg, st, 0)

        if action == "adopt_schedule":
            return _adopt_schedule(payload, reply_cfg, st, config_path, dry_run)

        if action == "view_schedule":
            if not st.active_plan:
                _safe_receipt("当前没有生效计划", reply_cfg, dry_run)
            else:
                _reconcile_plan_task_statuses(st.active_plan)
                push_active_plan_card(st.active_plan, reply_cfg, dry_run=dry_run)
            return _finish(cfg, st, 0)

        if action == "cancel_schedule":
            if st.active_plan and not dry_run:
                cancel_plan_tasks(st.active_plan.get("tasks") or [])
                st.active_plan = None
            _safe_receipt("✅ 已取消计划，未执行任务已删除", reply_cfg, dry_run)
            return _finish(cfg, st, 0)

        if action in ("warmup_now", "scheduled_warmup"):
            return _warmup(payload, reply_cfg, st, dry_run)

        if action == "recovery_snooze":
            minutes = max(1, min(int(payload.get("minutes") or 30), 24 * 60))
            due_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
            st.pending_recovery = {
                "provider": str(payload.get("provider") or ""),
                "window_key": str(payload.get("window_key") or ""),
                "due_at": due_at.isoformat(),
            }
            _safe_receipt(f"好，{minutes} 分钟后再提醒", reply_cfg, dry_run)
            return _finish(cfg, st, 0)

        if action == "schedule_remind_only":
            _safe_receipt("仅提醒功能未上线，请重新打开菜单选择可用操作", reply_cfg, dry_run)
            return _finish(cfg, st, 1)

        if action in ("recovery_skip", "tomorrow_skip", "skip"):
            if action == "recovery_skip":
                st.pending_recovery = None
            if action == "tomorrow_skip":
                _safe_receipt(
                    "好的，收到。好好休息，也是在给大脑充电 🌙",
                    reply_cfg,
                    dry_run,
                )
            return _finish(cfg, st, 0)

        _safe_receipt("该卡片已失效，请重新打开菜单", reply_cfg, dry_run)
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
        _safe_receipt("暂时没有可用于规划的 AI 工具", cfg, dry_run)
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
        _safe_receipt(f"❌ AI 工具状态已变化，请重新生成计划：{labels}", cfg, dry_run)
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
        _safe_receipt("预热工具无效", cfg, dry_run)
        return _finish(cfg, st, 4)
    scheduled = payload.get("action") == "scheduled_warmup"
    task = None
    if scheduled:
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
        if scheduled:
            _handle_scheduled_warmup_receipt(
                cfg,
                st,
                payload,
                task,
                "failed",
                error=str(exc),
                dry_run=dry_run,
            )
        else:
            _safe_receipt(f"❌ {provider_name} 预热失败：{exc}", cfg, dry_run)
        return _finish(cfg, st, 3)
    executed_at = datetime.now(timezone.utc)
    if task is not None:
        task["status"] = "executed"
        task["executed_at"] = executed_at.isoformat()
        cancel_plan_tasks([task])
    if window_key:
        warmed[provider_name] = window_key
        st.last_warmed_windows = warmed
    if scheduled:
        _handle_scheduled_warmup_receipt(
            cfg,
            st,
            payload,
            task,
            "executed",
            executed_at=executed_at,
            dry_run=dry_run,
        )
        return _finish(cfg, st, 0)
    _safe_receipt(
        f"✅ {provider_name} 已预热，新的额度窗口已开始",
        cfg,
        dry_run,
    )
    return _finish(cfg, st, 0)


def _handle_scheduled_warmup_receipt(
    cfg,
    st,
    payload: Mapping[str, Any],
    task,
    status: str,
    *,
    error: str = "",
    executed_at: datetime = None,
    dry_run: bool,
) -> None:
    receipt = {
        "key": _warmup_receipt_key(payload, status),
        "plan_id": str(payload.get("plan_id") or ""),
        "provider": str(payload.get("provider") or ""),
        "scheduled_for": str(payload.get("scheduled_for") or ""),
        "status": status,
        "executed_at": (executed_at or datetime.now(timezone.utc)).isoformat(),
    }
    if error:
        receipt["error"] = error[:200]
    active = st.active_plan or {}
    if _is_quiet_time(datetime.now().astimezone()):
        _queue_warmup_receipt(st, receipt)
        return
    text = _warmup_receipt_text(receipt, active)
    try:
        push_receipt(text, _notification_config(cfg, st), dry_run=dry_run)
    except NotifyError as exc:
        print(f"[预热回执失败] {exc}", file=sys.stderr)
        _queue_warmup_receipt(st, receipt)


def _queue_warmup_receipt(st, receipt: Dict[str, str]) -> None:
    pending = list(st.pending_warmup_receipts or [])
    key = receipt.get("key")
    if key and any(item.get("key") == key for item in pending):
        return
    pending.append(receipt)
    st.pending_warmup_receipts = pending


def _warmup_receipt_key(payload: Mapping[str, Any], status: str) -> str:
    return ":".join(
        [
            str(payload.get("plan_id") or ""),
            str(payload.get("provider") or ""),
            str(payload.get("scheduled_for") or ""),
            status,
        ]
    )


def _warmup_receipt_text(receipt: Mapping[str, str], active_plan=None) -> str:
    label = PROVIDER_LABEL.get(receipt.get("provider"), receipt.get("provider"))
    scheduled = _hhmm(receipt.get("scheduled_for"))
    if receipt.get("status") == "failed":
        lines = [f"❌ {label} {scheduled} 的预热失败。"]
        if receipt.get("error"):
            lines.append(f"原因：{receipt['error']}")
    else:
        lines = [f"✅ {label} {scheduled} 的预热已完成。"]
    next_time = _next_warmup_time(active_plan or {}, receipt)
    if next_time:
        lines.append(f"下一次预热：{next_time}。")
    elif active_plan:
        lines.append("今天的计划预热已全部完成。")
    return "\n".join(lines)


def _next_warmup_time(active_plan: Mapping[str, Any], receipt: Mapping[str, str]) -> str:
    scheduled_for = str(receipt.get("scheduled_for") or "")
    pending = []
    for task in active_plan.get("tasks") or []:
        if str(task.get("status") or "pending") != "pending":
            continue
        value = str(task.get("scheduled_for") or "")
        if scheduled_for and value <= scheduled_for:
            continue
        pending.append(value)
    return _hhmm(min(pending)) if pending else ""


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


def _is_quiet_time(local_now):
    return local_now.hour >= 23 or local_now.hour < 8


def _hhmm(value: Any) -> str:
    text = str(value or "")
    return text[11:16] if "T" in text else text[:5]


def _request_from_payload(payload: Mapping[str, Any], available_count: int):
    raw = dict(payload.get("request") or {})
    return parse_plan_request(raw, available_agent_count=available_count)


def _reconcile_plan_task_statuses(active_plan) -> None:
    tasks = active_plan.get("tasks") if isinstance(active_plan, dict) else None
    if not tasks:
        return
    now = datetime.now().astimezone()
    loaded = None
    for task in tasks:
        if str(task.get("status") or "pending") != "pending":
            continue
        scheduled = _parse_task_time(task.get("scheduled_for"))
        if scheduled is None or scheduled > now:
            continue
        label = str(task.get("label") or "")
        if not label:
            continue
        if loaded is None:
            loaded = _loaded_launchd_labels()
        if label in loaded:
            continue
        err_path = _task_log_path(task, ".err.log")
        if err_path and os.path.exists(err_path) and os.path.getsize(err_path) > 0:
            task["status"] = "failed"
            task["error"] = _read_short_file(err_path)
        else:
            task["status"] = "executed"
            task["executed_at"] = now.isoformat()
            task["status_inferred"] = True


def _parse_task_time(value):
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.astimezone()


def _loaded_launchd_labels():
    try:
        result = subprocess.run(
            ["launchctl", "list"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    return {
        line.rsplit(None, 1)[-1]
        for line in (result.stdout or "").splitlines()
        if "com.quota-butler.plan." in line
    }


def _task_log_path(task, suffix: str) -> str:
    plist = str(task.get("plist_path") or "")
    if plist.endswith(".plist"):
        return plist[:-6] + suffix
    return ""


def _read_short_file(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as stream:
            return stream.read()[-200:]
    except OSError:
        return ""


def _reply_config(cfg, payload: Mapping[str, Any]):
    message_id = str(
        payload.get("_message_id") or payload.get("message_id") or ""
    ).strip()
    if message_id:
        return replace(
            cfg,
            feishu=replace(
                cfg.feishu,
                chat_id="",
                user_id="",
                message_id=message_id,
            ),
        )
    operator_open_id = str(
        payload.get("_operator_open_id") or payload.get("operator_open_id") or ""
    ).strip()
    if operator_open_id:
        return replace(
            cfg,
            feishu=replace(
                cfg.feishu,
                chat_id="",
                user_id=operator_open_id,
                message_id="",
            ),
        )
    chat_id = str(payload.get("_chat_id") or payload.get("chat_id") or "").strip()
    if not chat_id:
        return cfg
    return replace(
        cfg,
        feishu=replace(cfg.feishu, chat_id=chat_id, user_id="", message_id=""),
    )


def _remember_notification_target(st, payload: Mapping[str, Any]) -> None:
    chat_type = str(
        payload.get("_chat_type")
        or payload.get("chat_type")
        or payload.get("_chat_mode")
        or payload.get("chat_mode")
        or ""
    ).strip().lower()
    if chat_type != "p2p":
        return
    chat_id = str(payload.get("_chat_id") or payload.get("chat_id") or "").strip()
    if not chat_id:
        return
    st.notification_target = {
        "chat_id": chat_id,
        "chat_type": "p2p",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


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
