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
    # 极值保底：只要不是恰好 0/100，少数侧至少留 1 格——
    # 否则 99% 看着像满、1% 看着像空，区分不出来。
    if 0.0 < value < 100.0:
        filled = max(1, min(width - 1, filled))
    return "█" * filled + "░" * (width - filled)


def usage_status(percent: float) -> str:
    """按「已用%」给状态（保留兼容；展示侧改用 remaining_status）。"""
    value = max(0.0, min(float(percent), 100.0))
    if value < 30:
        return "🟢 余量充足"
    if value < 70:
        return "🟡 正常使用"
    if value < 90:
        return "🟠 注意消耗"
    return "🔴 接近耗尽"


def remaining_status(remaining: float) -> str:
    """按「还剩%」给状态。与 Codex/Claude 原生「剩余」口径一致。"""
    value = max(0.0, min(float(remaining), 100.0))
    if value > 70:
        return "🟢 余量充足"
    if value > 30:
        return "🟡 正常使用"
    if value > 10:
        return "🟠 注意消耗"
    return "🔴 接近耗尽"


def _format_reset(window) -> str:
    if window.resets_at:
        return window.resets_at.astimezone().strftime("%m-%d %H:%M")
    return "暂无"


def _window_label(window) -> str:
    if window.kind == "five_hour":
        return "5 小时窗口"
    if window.kind == "weekly":
        return "7 天额度"
    if window.kind == "monthly":
        return "月度额度"
    seconds = window.window_seconds
    if seconds == 5 * 3600:
        return "5 小时窗口"
    if seconds == 7 * 86400:
        return "7 天额度"
    if seconds and 28 * 86400 <= seconds <= 31 * 86400:
        return "月度额度"
    if seconds and seconds % 86400 == 0:
        return f"{seconds // 86400} 天额度"
    if seconds and seconds % 3600 == 0:
        return f"{seconds // 3600} 小时窗口"
    return "额度窗口"


def _window_lines(window):
    remaining = 100 - window.utilization
    return [
        f"**{_window_label(window)}**",
        f"{usage_bar(remaining)} 还剩 **{remaining:.0f}%**",
        f"刷新：**{_format_reset(window)}**",
    ]


def build_status_card(statuses: Mapping[str, AgentStatus]) -> Dict[str, Any]:
    lines = ["**当前额度**", ""]
    for provider in ("cc", "codex"):
        status = statuses.get(provider)
        if status is None:
            continue
        label = PROVIDER_LABEL[provider]
        if status.state == AgentState.CONNECTED and status.usage:
            five = status.usage.five_hour
            rem5 = 100 - five.utilization
            lines.extend(
                [f"**{label}**", f"状态：{remaining_status(rem5)}", "", *_window_lines(five)]
            )
            rem7 = None
            if status.usage.seven_day:
                secondary = status.usage.seven_day
                rem7 = 100 - secondary.utilization
                lines.extend(["", *_window_lines(secondary)])
            # 木桶提示：周额度见底时，5 小时窗口再多也受其封顶。
            if rem7 is not None and rem7 < 20 and rem7 < rem5:
                lines.append(
                    f"⚠️ 本周额度仅剩 **{rem7:.0f}%**，是真正的上限——"
                    "5 小时窗口再充足也用不了多少。"
                )
        elif status.state == AgentState.TOKEN_STALE:
            lines.extend(
                [
                    f"**{label}**",
                    "🟡 **额度令牌已过期**",
                    "登录仍有效，用一次 Claude 即可自动刷新（无需重新登录）。",
                ]
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


def build_manual_warmup_card(statuses: Mapping[str, AgentStatus]) -> Dict[str, Any]:
    lines = [
        "**选择要立即预热的 AI 工具**",
        "点击后会立刻发起一次真实请求。",
    ]
    buttons = []
    for provider in ("cc", "codex"):
        status = statuses.get(provider)
        label = PROVIDER_LABEL[provider]
        if status and status.plan_eligible:
            callback = _callback("warmup_now", provider=provider)
            if status.usage and status.usage.five_hour.resets_at:
                callback["window_key"] = (
                    f"manual:{provider}:{status.usage.five_hour.resets_at.isoformat()}"
                )
            buttons.append(_button(label, "primary" if not buttons else "default", callback))
        elif status and status.state == AgentState.NEEDS_LOGIN:
            lines.append(f"{label}：需要重新登录")
        elif status and status.state == AgentState.CONNECTED:
            lines.append(f"{label}：当前额度不适合预热")
        else:
            lines.append(f"{label}：暂不可用")
    if not buttons:
        lines.append("")
        lines.append("暂时没有可预热的工具。")
    return _card("额度管家：手动预热", lines, buttons)


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


def _seg_weight(hours: float) -> int:
    """把段时长缩进飞书合法的 1–5 权重区间（>5 会被退化成内容宽度）。"""
    return max(2, min(5, int(round(float(hours)))))


def _seg_column(weight: int, bg: Optional[str], content: str) -> Dict[str, Any]:
    column = {
        "tag": "column",
        "width": "weighted",
        "weight": weight,
        "vertical_align": "center",
        "padding": "7px 2px",
        "elements": [{"tag": "markdown", "content": content, "text_align": "center"}],
    }
    if bg:
        column["background_style"] = bg
    return column


def _row(columns, margin: str) -> Dict[str, Any]:
    return {
        "tag": "column_set",
        "horizontal_spacing": "4px",
        "margin": margin,
        "columns": columns,
    }


def _schedule_timeline_elements(plan: SchedulePlan):
    def md(content):
        return {"tag": "markdown", "content": content, "text_align": "left"}

    ws, we = plan.work_start, plan.work_end
    first = plan.agents[0]
    first_label = PROVIDER_LABEL[first]
    fw = sorted(e.at for e in plan.events if e.agent == first)
    prep_start = fw[0] if fw else ws
    second_warm = fw[1] if len(fw) > 1 else we
    dual = len(plan.agents) >= 2 and any(
        e.agent == plan.agents[1] for e in plan.events
    )

    timeline = [_seg_column(1, "grey-200", f"{prep_start:%H:%M}\n预热")]

    if dual:
        relay = plan.agents[1]
        relay_label = PROVIDER_LABEL[relay]
        rw = sorted(e.at for e in plan.events if e.agent == relay)
        relay_at = rw[-1]
        w1_end = min(max(second_warm, ws), relay_at)
        w1_h = (w1_end - ws).total_seconds() / 3600
        w2_h = (relay_at - w1_end).total_seconds() / 3600
        relay_h = (we - relay_at).total_seconds() / 3600
        timeline.append(_seg_column(_seg_weight(w1_h), "blue-200", f"{ws:%H:%M}\n开工"))
        timeline.append(_seg_column(_seg_weight(w2_h), "blue-200", f"{second_warm:%H:%M}\n续上"))
        timeline.append(_seg_column(_seg_weight(relay_h), "wathet-200", f"{relay_at:%H:%M}\n{relay_label}"))
        headline = md(
            f"**明天 {ws:%H:%M}–{we:%H:%M}：{first_label} 为主，{relay_label} 接力**"
        )
        note = md(f"将创建 **{len(plan.events)}** 个预热任务；每次预热都会发起一次真实请求。")
    else:
        w1_end = min(max(second_warm, ws), we)
        w1_h = (w1_end - ws).total_seconds() / 3600
        w2_h = (we - w1_end).total_seconds() / 3600
        timeline.append(_seg_column(_seg_weight(w1_h), "blue-200", f"{ws:%H:%M}\n开工"))
        timeline.append(_seg_column(_seg_weight(w2_h), "blue-200", f"{second_warm:%H:%M}\n续上"))
        headline = md(f"**明天 {ws:%H:%M}–{we:%H:%M}：{first_label}**")
        note = md(f"将创建 **{len(plan.events)}** 个预热任务；每次预热都会发起一次真实请求。")

    elements = [
        headline,
        _row(timeline, "8px 0px 6px 0px"),
        note,
    ]
    return elements


def build_schedule_card(plan: SchedulePlan) -> Dict[str, Any]:
    record = plan_record(plan)
    request = _request_dict(plan.request)
    elements = _schedule_timeline_elements(plan)
    buttons = [
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
    ]
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
        "config": {"summary": {"content": "额度管家：明日计划预览"}},
        "body": {"elements": elements},
    }


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
            status = _task_status_text(str(task.get("status") or "pending"))
            lines.append(
                f"{status[0]} **{_hhmm(task.get('scheduled_for'))}** · "
                f"{PROVIDER_LABEL.get(task.get('provider'), task.get('provider'))}"
                f" · {status[1]}"
            )
    else:
        for event in record.get("events") or []:
            lines.append(
                f"⏳ **{_hhmm(event.get('at'))}** · "
                f"{PROVIDER_LABEL.get(event.get('agent'), event.get('agent'))}"
                " · 未执行"
            )
    return _card(
        "额度管家：当前计划",
        lines,
        [_button("取消所有计划", "danger", _callback("cancel_schedule"))],
    )


def build_command_menu_card() -> Dict[str, Any]:
    return _card(
        "额度管家",
        ["**想做什么？**"],
        [
            _button("查询额度", "primary", _callback("query_status")),
            _button("查看当前计划", "default", _callback("view_schedule")),
            _button("立即预热", "default", _callback("manual_warmup")),
            _button("设置明日计划", "default", _callback("schedule_intent", intent="tomorrow")),
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


def _task_status_text(status: str):
    if status == "executed":
        return "✅", "已执行"
    if status == "failed":
        return "❌", "失败"
    return "⏳", "未执行"


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
    command_name = "+messages-send"
    if config.feishu.message_id:
        command_name = "+messages-reply"
        target = ["--message-id", config.feishu.message_id]
    elif config.feishu.chat_id:
        target = ["--chat-id", config.feishu.chat_id]
    elif config.feishu.user_id:
        target = ["--user-id", config.feishu.user_id]
    else:
        raise NotifyError("config.feishu 未配置 message_id / chat_id / user_id")
    command = [
        "lark-cli",
        "im",
        command_name,
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
