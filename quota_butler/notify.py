"""Feishu CardKit cards for Quota Butler V3."""

from __future__ import annotations

import json
import math
import subprocess
from datetime import date
from typing import Any, Dict, Mapping, Optional

from .agent_status import AgentState, AgentStatus
from .config import Config
from .plan_tasks import plan_record
from .planner import AGENT_LABELS, SchedulePlan
from .schedule_flow import PlanRequest, flow_payload

PROVIDER_LABEL = AGENT_LABELS


class NotifyError(Exception):
    pass


def usage_bar(percent: float, width: int = 10) -> str:
    if width <= 0:
        return ""
    value = max(0.0, min(float(percent), 100.0))
    filled = min(width, int(math.floor(value * width / 100 + 0.5)))
    return "█" * filled + "░" * (width - filled)


def usage_status(percent: float) -> str:
    value = max(0.0, min(float(percent), 100.0))
    if value < 30:
        return "🟢 余量充足"
    if value < 70:
        return "🟡 正常使用"
    if value < 90:
        return "🟠 注意消耗"
    return "🔴 接近耗尽"


def build_status_card(statuses: Mapping[str, AgentStatus]) -> Dict[str, Any]:
    lines = ["**当前额度**", ""]
    for provider in ("cc", "codex"):
        status = statuses.get(provider)
        if status is None:
            continue
        label = PROVIDER_LABEL[provider]
        if status.state == AgentState.CONNECTED and status.usage:
            five = status.usage.five_hour
            reset = (
                five.resets_at.astimezone().strftime("%m-%d %H:%M")
                if five.resets_at
                else "暂无"
            )
            lines.extend(
                [
                    f"**{label} · 5 小时窗口**",
                    f"{usage_bar(five.utilization)} **{five.utilization:.0f}%**",
                    f"{usage_status(five.utilization)} · 恢复：**{reset}**",
                ]
            )
            if status.usage.seven_day:
                seven = status.usage.seven_day
                lines.append(
                    f"7 天额度：{usage_bar(seven.utilization)} "
                    f"**{seven.utilization:.0f}%**"
                )
        elif status.state == AgentState.NEEDS_LOGIN:
            instruction = (
                "`claude auth login`"
                if provider == "cc"
                else "`codex login`"
            )
            lines.extend(
                [
                    f"**{label}**",
                    "🟡 **需要重新登录**",
                    f"请在本机运行 {instruction}",
                ]
            )
        elif status.state == AgentState.UNAVAILABLE:
            lines.extend(
                [
                    f"**{label}**",
                    "🟠 **暂时无法读取**",
                    "已检测到安装，稍后可重新查询。",
                ]
            )
        else:
            lines.extend([f"**{label}**", "⚪ **未检测到安装**"])
        lines.append("")
    return _card("额度管家：当前额度", lines)


def build_recovery_card(provider: str, window_key: str) -> Dict[str, Any]:
    label = PROVIDER_LABEL.get(provider, provider)
    return _card(
        f"{label} 已恢复",
        [f"⚡ **{label} 已恢复。接下来准备重点使用吗？**"],
        [
            _button(
                "立即预热",
                "primary",
                _callback("warmup_now", provider=provider, window_key=window_key),
            ),
            _button(
                "30 分钟后提醒",
                "default",
                _callback(
                    "recovery_snooze",
                    provider=provider,
                    window_key=window_key,
                    minutes=30,
                ),
            ),
            _button(
                "暂时不用",
                "default",
                _callback("recovery_skip", provider=provider, window_key=window_key),
            ),
        ],
    )


def build_bedtime_card(
    statuses: Optional[Mapping[str, AgentStatus]] = None,
    recovered_provider: str = "",
) -> Dict[str, Any]:
    lines = []
    if recovered_provider:
        lines.append(f"⚡ {PROVIDER_LABEL[recovered_provider]} 刚刚恢复。")
        lines.append("")
    if statuses:
        connected = [
            PROVIDER_LABEL[name]
            for name, status in statuses.items()
            if status.schedulable
        ]
        if connected:
            lines.append(f"当前可用：**{' + '.join(connected)}**")
            lines.append("")
    lines.append("🌙 **明天有重度使用 AI 的计划吗？**")
    target = date.today().fromordinal(date.today().toordinal() + 1)
    return _card(
        "额度管家：明日计划",
        lines,
        [
            _button(
                "明天要重度使用",
                "primary",
                _callback(
                    "schedule_intent",
                    intent="tomorrow",
                    target_date=target.isoformat(),
                ),
            ),
            _button("明天不用", "default", _callback("tomorrow_skip")),
        ],
    )


def build_time_mode_card(target_date: date) -> Dict[str, Any]:
    base = PlanRequest(target_date=target_date)
    return _card(
        "选择重度使用时间",
        [
            f"**{target_date.strftime('%m 月 %d 日')} 的重度使用时间**",
            "",
            "只需要告诉我开始时间，或一个明确区间。",
        ],
        [
            _button(
                "从某时开始",
                "primary",
                flow_payload("edit_time_point", base),
            ),
            _button(
                "指定时间区间",
                "default",
                flow_payload(
                    "edit_time_range",
                    PlanRequest(target_date, "range", "09:00", "17:00", "auto"),
                ),
            ),
        ],
    )


def build_time_card(
    request: PlanRequest,
    *,
    error: str = "",
) -> Dict[str, Any]:
    fields = [
        {
            "tag": "picker_time",
            "name": "work_start",
            "placeholder": {"tag": "plain_text", "content": "选择开始时间"},
            "initial_time": request.work_start,
            "required": True,
        }
    ]
    if request.time_mode == "range":
        fields.append(
            {
                "tag": "picker_time",
                "name": "work_end",
                "placeholder": {"tag": "plain_text", "content": "选择结束时间"},
                "initial_time": request.work_end,
                "required": True,
            }
        )
    fields.append(
        {
            "tag": "button",
            "name": "submit_plan_time",
            "text": {"tag": "plain_text", "content": "生成计划"},
            "type": "primary",
            "width": "fill",
            "form_action_type": "submit",
            "behaviors": [
                {
                    "type": "callback",
                    "value": flow_payload("generate_plan", request),
                }
            ],
        }
    )
    lines = ["**选择重度使用时间**"]
    if error:
        lines.extend(["", f"❌ {error}"])
    return {
        "schema": "2.0",
        "config": {"summary": {"content": "额度管家：设置使用时间"}},
        "body": {
            "elements": [
                {"tag": "markdown", "content": "\n".join(lines)},
                {"tag": "form", "name": "v3_plan_time", "elements": fields},
            ]
        },
    }


def build_agent_control_card(
    request: PlanRequest,
    statuses: Mapping[str, AgentStatus],
) -> Dict[str, Any]:
    available = [
        provider for provider in ("cc", "codex")
        if statuses.get(provider) and statuses[provider].schedulable
    ]
    if len(available) <= 1:
        label = PROVIDER_LABEL[available[0]] if available else "可用 Agent"
        return _card(
            "更换 AI 工具",
            [f"当前仅检测到 {label}。", "", "重新检测后会按最新状态生成计划。"],
            [
                _button(
                    "重新检测",
                    "primary",
                    _callback(
                        "redetect_agents",
                        request=_request_dict(request),
                    ),
                )
            ],
        )
    buttons = []
    for label, strategy in (
        ("Claude Code", "cc"),
        ("Codex", "codex"),
        ("两个都用", "both"),
    ):
        candidate = PlanRequest(
            request.target_date,
            request.time_mode,
            request.work_start,
            request.work_end,
            strategy,
        )
        buttons.append(
            _button(
                label,
                "primary" if strategy == request.agent_strategy else "default",
                {
                    **flow_payload("generate_plan", candidate),
                    "agent_strategy": strategy,
                },
            )
        )
    return _card(
        "更换 AI 工具",
        ["**明天想使用哪个 AI 工具？**"],
        buttons,
    )


def build_schedule_card(plan: SchedulePlan) -> Dict[str, Any]:
    labels = " + ".join(PROVIDER_LABEL[agent] for agent in plan.agents)
    lines = [
        f"**明天 {plan.work_start:%H:%M}–{plan.work_end:%H:%M}，"
        f"优先使用 {labels}。**",
        "",
    ]
    for event in plan.events:
        lines.append(
            f"**{event.at:%H:%M}** · {PROVIDER_LABEL[event.agent]} · {event.purpose}"
        )
    lines.extend(
        [
            "",
            f"预计连续覆盖：**{plan.work_start:%H:%M}–{plan.work_end:%H:%M}**",
            f"安排原因：{plan.reason}",
        ]
    )
    if "cc" in plan.agents:
        lines.extend(["", "说明：Claude Code 预热会产生一次真实请求。"])
    record = plan_record(plan)
    request = _request_dict(plan.request)
    return _card(
        "额度管家：明日计划预览",
        lines,
        [
            _button("采用计划", "primary", _callback("adopt_schedule", plan=record)),
            _button(
                "更换 AI 工具",
                "default",
                _callback("adjust_schedule_agents", request=request),
            ),
            _button(
                "修改使用时间",
                "default",
                _callback("adjust_schedule_time", request=request),
            ),
            _button("仅提醒", "default", _callback("schedule_remind_only")),
        ],
    )


def build_active_plan_card(record: Mapping[str, Any]) -> Dict[str, Any]:
    start = _hhmm(record.get("work_start"))
    end = _hhmm(record.get("work_end"))
    labels = " + ".join(
        PROVIDER_LABEL.get(agent, str(agent))
        for agent in record.get("agents") or []
    )
    lines = [
        f"**当前计划 · {start}–{end}**",
        f"AI 工具：**{labels or '未记录'}**",
        "",
    ]
    tasks = record.get("tasks") or []
    if tasks:
        for task in tasks:
            lines.append(
                f"⏳ **{_hhmm(task.get('scheduled_for'))}** · "
                f"{PROVIDER_LABEL.get(task.get('provider'), task.get('provider'))}"
            )
    else:
        for event in record.get("events") or []:
            lines.append(
                f"⏳ **{_hhmm(event.get('at'))}** · "
                f"{PROVIDER_LABEL.get(event.get('agent'), event.get('agent'))}"
            )
    return _card(
        "额度管家：当前计划",
        lines,
        [_button("取消计划", "danger", _callback("cancel_schedule"))],
    )


def build_command_menu_card() -> Dict[str, Any]:
    return _card(
        "额度管家",
        ["**想做什么？**"],
        [
            _button("当前额度", "primary", _callback("query_status")),
            _button("明日计划", "default", _callback("schedule_intent", intent="tomorrow")),
            _button("查看当前计划", "default", _callback("view_schedule")),
        ],
    )


def push_status_card(statuses, config: Config, dry_run: bool = False):
    return push_interactive(build_status_card(statuses), config, dry_run)


def push_schedule_card(plan: SchedulePlan, config: Config, dry_run: bool = False):
    return push_interactive(build_schedule_card(plan), config, dry_run)


def push_active_plan_card(record, config: Config, dry_run: bool = False):
    return push_interactive(build_active_plan_card(record), config, dry_run)


def push_command_menu_card(config: Config, dry_run: bool = False):
    return push_interactive(build_command_menu_card(), config, dry_run)


def push_interactive(card, config: Config, dry_run: bool = False):
    if dry_run:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return None
    return _send("interactive", json.dumps(card, ensure_ascii=False), config)


def push_receipt(text: str, config: Config, dry_run: bool = False):
    if dry_run:
        print(f"[dry-run] {text}")
        return None
    return _send("text", json.dumps({"text": text}, ensure_ascii=False), config)


def _card(summary: str, lines, buttons=None):
    elements = [{"tag": "markdown", "content": "\n".join(lines)}]
    if buttons:
        for offset in range(0, len(buttons), 2):
            elements.append(
                {
                    "tag": "column_set",
                    "columns": [
                        {"tag": "column", "elements": [button]}
                        for button in buttons[offset:offset + 2]
                    ],
                }
            )
    return {
        "schema": "2.0",
        "config": {"summary": {"content": summary}},
        "body": {"elements": elements},
    }


def _button(text: str, button_type: str, value: Dict[str, Any]):
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": button_type,
        "width": "fill",
        "behaviors": [{"type": "callback", "value": value}],
    }


def _callback(action: str, **fields):
    return {"cmd": "quota", "action": action, **fields}


def _request_dict(request: PlanRequest):
    return {
        "target_date": request.target_date.isoformat(),
        "time_mode": request.time_mode,
        "work_start": request.work_start,
        "work_end": request.work_end,
        "agent_strategy": request.agent_strategy,
    }


def _hhmm(value: Any) -> str:
    text = str(value or "")
    try:
        return text[11:16] if "T" in text else text[:5]
    except (TypeError, IndexError):
        return "?"


def _send(msg_type: str, content_json: str, config: Config) -> str:
    target = []
    if config.feishu.chat_id:
        target = ["--chat-id", config.feishu.chat_id]
    elif config.feishu.user_id:
        target = ["--user-id", config.feishu.user_id]
    else:
        raise NotifyError("config.feishu 未配置 chat_id / user_id")
    command = [
        "lark-cli",
        "im",
        "+messages-send",
        "--as",
        "bot",
        *target,
        "--msg-type",
        msg_type,
        "--content",
        content_json,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise NotifyError(f"调用 lark-cli 失败: {exc}") from exc
    if result.returncode != 0:
        raise NotifyError(
            f"lark-cli 退出码 {result.returncode}: {result.stderr.strip()[:200]}"
        )
    return result.stdout.strip()
