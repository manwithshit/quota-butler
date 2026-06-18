"""S4 · 卡片回调处理器（承接「开 / 不开」点击）。

## 它在闭环里的位置

quota-butler 的感知端（main.py）是 launchd 定时拉起、跑完即退的进程，**不常驻**，
所以按钮点击不可能落回那个已退出的进程。回调走本机 bridge fork 的 quota 命令：

    用户点【🔥 开】
      → 飞书 → lark-channel-bridge-quota
      → bridge 校验操作者权限，以固定 argv 启动本处理器
      → 完整 callback payload 通过 stdin JSON 传入
      → 本处理器：action=warmup → 调 provider.warmup() → 发回执到群
                  action=skip   → 静默写状态

这样「点开 → 预热 → 回执」是确定性的、可复现、可截图，不依赖 agent 自由发挥。

## 集成契约（给私人 bridge fork）

卡片 callback value 使用 `{"cmd": "quota", "action": ...}`。bridge 把完整 JSON
通过 stdin 交给：

    python3 -m quota_butler.handler --config ~/.quota-butler/config.yaml

## 防重复

同一个 resets_at 窗口若已预热过（state.last_warmed_reset_at 命中），再点「开」只回一句
「该窗口已开过」，不重复烧 token。
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, time, timedelta, timezone

from . import config as config_mod
from . import state as state_mod
from .notify import (
    PROVIDER_LABEL,
    NotifyError,
    build_schedule_intensity_card,
    build_schedule_scenario_card,
    build_schedule_summary_card,
    build_schedule_task_card,
    build_schedule_time_card,
    push_active_plan_card,
    push_guided_schedule_card,
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
from .planner import parse_agents, plan_from_config, plan_from_preferences
from .schedule_flow import SchedulePreferences, parse_preferences, validate_flow_context
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

    if action == "schedule_intent":
        intent = str((payload or {}).get("intent") or "")
        target_date = date.today()
        if "明天" in intent or "tomorrow" in intent.lower():
            target_date += timedelta(days=1)
        try:
            profile = _schedule_profile(st, payload or {})
            if profile.get("daily_scenario"):
                preferences = parse_preferences(profile)
                card = build_schedule_task_card(target_date, preferences)
            else:
                card = build_schedule_scenario_card(
                    target_date,
                    SchedulePreferences(),
                    return_step="task",
                )
            push_guided_schedule_card(card, cfg, dry_run=dry_run)
        except NotifyError as e:
            print(f"[回调] 规划引导卡发送失败：{e}", file=sys.stderr)
            state_mod.save(cfg.resolved_state_path, st)
            return 3
        print(f"[回调] 已开始规划流程：{target_date.isoformat()}")
        state_mod.save(cfg.resolved_state_path, st)
        return 0

    if action == "schedule_flow":
        try:
            target_date = validate_flow_context(payload or {})
        except ValueError:
            _safe_receipt("规划卡已失效，请重新规划", cfg, dry_run)
            state_mod.save(cfg.resolved_state_path, st)
            return 4

        raw_preferences = dict((payload or {}).get("preferences") or {})
        form_value = (payload or {}).get("form_value")
        if isinstance(form_value, dict):
            raw_preferences.update({
                key: form_value[key]
                for key in ("work_start", "work_end", "daily_scenario")
                if key in form_value
            })
        step = str((payload or {}).get("step") or "")
        if step == "scenario_saved" and not str(
            raw_preferences.get("daily_scenario") or ""
        ).strip():
            try:
                fallback = parse_preferences(
                    (payload or {}).get("preferences") or {}
                )
                card = build_schedule_scenario_card(
                    target_date,
                    fallback,
                    return_step=str((payload or {}).get("return_step") or "task"),
                    error="请填写日常使用场景",
                )
                push_guided_schedule_card(card, cfg, dry_run=dry_run)
            except (ValueError, NotifyError) as send_error:
                print(f"[回调] 场景卡发送失败：{send_error}", file=sys.stderr)
                state_mod.save(cfg.resolved_state_path, st)
                return 3
            state_mod.save(cfg.resolved_state_path, st)
            return 0
        try:
            preferences = parse_preferences(raw_preferences)
        except ValueError as e:
            if not isinstance(form_value, dict):
                _safe_receipt(f"规划偏好无效：{e}，请重新规划", cfg, dry_run)
                state_mod.save(cfg.resolved_state_path, st)
                return 4
            try:
                fallback = parse_preferences((payload or {}).get("preferences") or {})
                card = build_schedule_time_card(
                    target_date,
                    fallback,
                    error=str(e),
                )
                push_guided_schedule_card(card, cfg, dry_run=dry_run)
            except (ValueError, NotifyError) as send_error:
                print(f"[回调] 时间卡发送失败：{send_error}", file=sys.stderr)
                state_mod.save(cfg.resolved_state_path, st)
                return 3
            state_mod.save(cfg.resolved_state_path, st)
            return 0

        if step == "scenario_saved":
            profiles = dict(st.schedule_profiles or {})
            profiles[_schedule_profile_key(payload or {})] = {
                "daily_scenario": preferences.daily_scenario,
            }
            st.schedule_profiles = profiles
            return_step = str((payload or {}).get("return_step") or "task")
            if return_step == "summary":
                card = build_schedule_summary_card(target_date, preferences)
            else:
                card = build_schedule_task_card(target_date, preferences)
            try:
                push_guided_schedule_card(card, cfg, dry_run=dry_run)
            except NotifyError as e:
                print(f"[回调] 保存场景后发卡失败：{e}", file=sys.stderr)
                state_mod.save(cfg.resolved_state_path, st)
                return 3
            state_mod.save(cfg.resolved_state_path, st)
            return 0

        if step == "generate":
            available, _ = _agent_availability(("cc", "codex"))
            if not available:
                _safe_receipt("暂时没有可用 Agent，无法生成计划", cfg, dry_run)
                state_mod.save(cfg.resolved_state_path, st)
                return 4
            try:
                plan = plan_from_preferences(
                    preferences,
                    target_date=target_date,
                    agents=available,
                )
                push_schedule_card(plan, cfg, dry_run=dry_run)
            except (ValueError, NotifyError) as e:
                print(f"[回调] 生成计划失败：{e}", file=sys.stderr)
                state_mod.save(cfg.resolved_state_path, st)
                return 3
            state_mod.save(cfg.resolved_state_path, st)
            return 0

        builders = {
            "task": build_schedule_task_card,
            "intensity": build_schedule_intensity_card,
            "time": build_schedule_time_card,
            "summary": build_schedule_summary_card,
        }
        if step == "scenario":
            try:
                card = build_schedule_scenario_card(
                    target_date,
                    preferences,
                    return_step="summary",
                )
                push_guided_schedule_card(card, cfg, dry_run=dry_run)
            except NotifyError as e:
                print(f"[回调] 场景卡发送失败：{e}", file=sys.stderr)
                state_mod.save(cfg.resolved_state_path, st)
                return 3
            state_mod.save(cfg.resolved_state_path, st)
            return 0
        builder = builders.get(step)
        if builder is None:
            print(f"[回调] 未知规划步骤={step!r}", file=sys.stderr)
            state_mod.save(cfg.resolved_state_path, st)
            return 1
        try:
            card = builder(target_date, preferences)
            push_guided_schedule_card(card, cfg, dry_run=dry_run)
        except NotifyError as e:
            print(f"[回调] 规划卡发送失败：{e}", file=sys.stderr)
            state_mod.save(cfg.resolved_state_path, st)
            return 3
        state_mod.save(cfg.resolved_state_path, st)
        return 0

    if action == "query_status":
        results = []
        for name in ("cc", "codex"):
            try:
                usage = get_provider(name).read_usage()
                results.append((name, usage, None))
            except (ProviderError, NotImplementedError) as e:
                results.append((name, None, str(e)))
        try:
            push_status_card(results, cfg, dry_run=dry_run)
        except NotifyError as e:
            print(f"[回调] 状态卡发送失败：{e}", file=sys.stderr)
            state_mod.save(cfg.resolved_state_path, st)
            return 3
        print("[回调] 已发送额度状态卡")
        state_mod.save(cfg.resolved_state_path, st)
        return 0

    if action == "adopt_schedule":
        if st.active_plan and st.active_plan.get("status") == "active":
            _safe_receipt("已有生效计划，请先取消后再采用新计划", cfg, dry_run)
            state_mod.save(cfg.resolved_state_path, st)
            return 4
        candidate = (payload or {}).get("plan")
        if not isinstance(candidate, dict) or candidate.get("plan_version") != 2:
            _safe_receipt("旧计划已失效，请重新规划", cfg, dry_run)
            state_mod.save(cfg.resolved_state_path, st)
            return 4
        try:
            record = validate_plan_record(candidate)
            planned_agents = tuple(dict.fromkeys(
                str(agent)
                for agent in (
                    record.get("agents")
                    or [event.get("agent") for event in record.get("events") or []]
                )
                if agent
            ))
            _, failures = _agent_availability(planned_agents)
            if failures:
                detail = "；".join(failures)
                _safe_receipt(
                    f"❌ 计划包含不可用 Agent，已拒绝采用：{detail}",
                    cfg,
                    dry_run,
                )
                state_mod.save(cfg.resolved_state_path, st)
                return 4
            tasks = [] if dry_run else install_plan_tasks(
                record, cfg, config_path=config_path
            )
        except PlanTaskError as e:
            print(f"[回调] 采用计划失败：{e}", file=sys.stderr)
            _safe_receipt(f"❌ 采用计划失败：{e}", cfg, dry_run)
            state_mod.save(cfg.resolved_state_path, st)
            return 3
        if not dry_run:
            record["status"] = "active"
            record["adopted_at"] = datetime.now(timezone.utc).isoformat()
            record["tasks"] = tasks
            st.active_plan = record
            state_mod.save(cfg.resolved_state_path, st)
        _safe_receipt(f"✅ 已采用计划，已创建 {len(tasks)} 个预热任务", cfg, dry_run)
        print(f"[回调] 已采用计划 {record['plan_id']}，任务数={len(tasks)}")
        return 0

    if action == "cancel_schedule":
        if not st.active_plan:
            _safe_receipt("当前没有生效计划", cfg, dry_run)
            state_mod.save(cfg.resolved_state_path, st)
            return 0
        if not dry_run:
            cancel_plan_tasks(st.active_plan.get("tasks") or [])
            st.active_plan = None
            state_mod.save(cfg.resolved_state_path, st)
        _safe_receipt("✅ 已取消计划，未执行任务已删除", cfg, dry_run)
        print("[回调] 已取消当前计划")
        return 0

    if action == "view_schedule":
        if not st.active_plan:
            _safe_receipt("当前没有生效计划", cfg, dry_run)
            state_mod.save(cfg.resolved_state_path, st)
            return 0
        try:
            push_active_plan_card(st.active_plan, cfg, dry_run=dry_run)
        except NotifyError as e:
            print(f"[回调] 当前计划卡发送失败：{e}", file=sys.stderr)
            return 3
        state_mod.save(cfg.resolved_state_path, st)
        return 0

    if action == "schedule_remind_only":
        _safe_receipt("已保留为提醒，不会创建本地预热任务", cfg, dry_run)
        state_mod.save(cfg.resolved_state_path, st)
        return 0

    if action == "oneup_start":
        provider_name = str((payload or {}).get("provider") or "")
        window_key = str((payload or {}).get("window_key") or "")
        label = PROVIDER_LABEL.get(provider_name, provider_name)
        if provider_name != "codex":
            _safe_receipt("真实预热仅允许 Codex", cfg, dry_run)
            state_mod.save(cfg.resolved_state_path, st)
            return 4
        if window_key and st.last_oneup_started_window == window_key:
            _safe_receipt(f"{label} 在这个恢复窗口已经启动过，不重复预热", cfg, dry_run)
            return 0
        if dry_run:
            _safe_receipt(f"✅ {label} 已启动（dry-run）", cfg, True)
            print(f"[回调] one-up 已启动 {label}（dry-run）")
            return 0
        try:
            get_provider(provider_name).warmup(cfg.warmup_prompt)
        except (ProviderError, NotImplementedError, ValueError) as e:
            _safe_receipt(f"❌ 启动失败：{e}", cfg, dry_run)
            print(f"[回调] one-up 启动失败：{e}", file=sys.stderr)
            return 3
        _safe_receipt(f"✅ {label} 已启动，新窗口开始工作", cfg, dry_run)
        st.last_oneup_started_window = window_key or None
        st.pending_oneup = None
        state_mod.save(cfg.resolved_state_path, st)
        print(f"[回调] one-up 已启动 {label}")
        return 0

    if action == "oneup_snooze":
        try:
            minutes = int((payload or {}).get("minutes", 30))
        except (TypeError, ValueError):
            minutes = 30
        minutes = max(5, min(minutes, 24 * 60))
        if dry_run:
            _safe_receipt(f"已延后 {minutes} 分钟提醒（dry-run）", cfg, True)
            return 0
        st.muted_until = (
            datetime.now(timezone.utc) + timedelta(minutes=minutes)
        ).isoformat()
        st.pending_oneup = {
            "provider": str((payload or {}).get("provider") or ""),
            "window_key": str((payload or {}).get("window_key") or ""),
        }
        state_mod.save(cfg.resolved_state_path, st)
        _safe_receipt(f"已延后 {minutes} 分钟提醒", cfg, dry_run)
        return 0

    if action == "oneup_mute_today":
        if dry_run:
            _safe_receipt("今天不再提醒 one-up（dry-run）", cfg, True)
            return 0
        local_now = datetime.now().astimezone()
        tomorrow = local_now.date() + timedelta(days=1)
        local_midnight = datetime.combine(
            tomorrow, time.min, tzinfo=local_now.tzinfo
        )
        st.muted_until = local_midnight.astimezone(timezone.utc).isoformat()
        st.pending_oneup = None
        state_mod.save(cfg.resolved_state_path, st)
        _safe_receipt("今天不再提醒 one-up", cfg, dry_run)
        return 0

    if action == "scheduled_warmup":
        plan_id = str((payload or {}).get("plan_id") or "")
        provider_name = str((payload or {}).get("provider") or "")
        scheduled_for = str((payload or {}).get("scheduled_for") or "")
        task = _matching_plan_task(st.active_plan, plan_id, provider_name, scheduled_for)
        if task is None:
            print("[回调] 定时预热与当前 active plan 不匹配，拒绝执行", file=sys.stderr)
            state_mod.save(cfg.resolved_state_path, st)
            return 4
        if provider_name != "codex":
            _safe_receipt("真实预热仅允许 Codex", cfg, dry_run)
            state_mod.save(cfg.resolved_state_path, st)
            return 4
        if task.get("status") == "executed":
            print("[回调] 定时预热已执行过，跳过")
            return 0
        if dry_run:
            _safe_receipt(
                f"✅ {PROVIDER_LABEL.get(provider_name, provider_name)} 定时预热完成（dry-run）",
                cfg,
                True,
            )
            return 0
        try:
            get_provider(provider_name).warmup(cfg.warmup_prompt)
        except (ProviderError, NotImplementedError) as e:
            _safe_receipt(f"❌ 定时预热失败：{e}", cfg, False)
            print(f"[回调] 定时预热失败：{e}", file=sys.stderr)
            return 3
        task["status"] = "executed"
        task["executed_at"] = datetime.now(timezone.utc).isoformat()
        state_mod.save(cfg.resolved_state_path, st)
        cancel_plan_tasks([task])
        label = PROVIDER_LABEL.get(provider_name, provider_name)
        _safe_receipt(f"✅ {label} 已按计划完成预热，可以接力", cfg, False)
        print(f"[回调] {label} 定时预热完成")
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

    provider_name = "codex"
    print(f"[回调] 点「开」→ 用 {provider_name} 预热：{cfg.warmup_prompt!r}")
    if dry_run:
        # dry-run 绝不真烧 token：只模拟，不调 warmup()
        print("[dry-run] 跳过真实 claude -p 调用")
        _safe_receipt("✅ 已开窗，新窗口从现在起算", cfg, dry_run)
        print("[回调] 预热完成（dry-run 模拟），已回执")
        return 0
    provider = get_provider(provider_name)
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


def _matching_plan_task(active_plan, plan_id: str, provider: str, scheduled_for: str):
    if not active_plan or active_plan.get("status") != "active":
        return None
    if active_plan.get("plan_id") != plan_id:
        return None
    for task in active_plan.get("tasks") or []:
        if task.get("provider") == provider and task.get("scheduled_for") == scheduled_for:
            return task
    return None


def _agent_availability(agent_names):
    available = []
    failures = []
    for name in agent_names:
        label = PROVIDER_LABEL.get(name, name)
        try:
            get_provider(name).read_usage()
            available.append(name)
        except (ProviderError, NotImplementedError, ValueError) as exc:
            failures.append(f"{label} 不可用：{exc}")
    return tuple(available), tuple(failures)


def _schedule_profile_key(payload: dict) -> str:
    operator = str((payload or {}).get("_operator_open_id") or "").strip()
    if operator:
        return operator
    chat = str((payload or {}).get("_chat_id") or "").strip()
    return f"chat:{chat}" if chat else "default"


def _schedule_profile(st, payload: dict) -> dict:
    profiles = st.schedule_profiles or {}
    profile = profiles.get(_schedule_profile_key(payload))
    return dict(profile) if isinstance(profile, dict) else {}


def _read_payload(raw: str = "") -> dict:
    raw = raw or sys.stdin.read()
    raw = raw.strip()
    if not raw:
        raise ValueError("未提供 payload（参数或 stdin 均为空）")
    return json.loads(raw)


def main(argv=None) -> int:
    import argparse

    args_in = sys.argv[1:] if argv is None else list(argv)
    if args_in and args_in[0].endswith(("handler", "handler.py")):
        args_in = args_in[1:]
    parser = argparse.ArgumentParser(prog="quota-butler-handler")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("payload", nargs="?")
    args = parser.parse_args(args_in)
    try:
        payload = _read_payload(args.payload or "")
    except (ValueError, json.JSONDecodeError) as e:
        print(f"[回调] payload 解析失败：{e}", file=sys.stderr)
        return 2
    return handle(payload, config_path=args.config, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
