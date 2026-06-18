"""飞书消息：提醒卡（带【开/不开】）· 回执 · 状态卡（群聊查询用）。

用 lark-cli 发 CardKit 2.0 卡片 / 文本到目标群 / 私聊，必须以 bot 身份
（user 身份缺 im:message.send_as_user scope）。

回调机制：私人 bridge fork 的内置 quota 命令。卡片按钮 callback 的 value 带
{"cmd": "quota", "action": ...}，bridge 完成权限检查后把完整 payload 通过 stdin
交给 quota_butler.handler。本模块只负责把消息发出去。
"""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import replace
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .config import Config
from .plan_tasks import plan_record
from .planner import SchedulePlan
from .rules import Decision
from .providers.base import Usage
from .schedule_flow import (
    INTENSITY_LABELS,
    TASK_TYPE_LABELS,
    SchedulePreferences,
    flow_payload,
)

PROVIDER_LABEL = {"cc": "Claude Code", "codex": "Codex"}
MODE_LABEL = {"sustain": "不断粮模式", "balanced": "平衡模式", "savings": "节省模式"}


class NotifyError(Exception):
    pass


# ---- 公共：窗口标签 + lark-cli 发送 --------------------------------------

def window_label(window_seconds: Optional[int]) -> str:
    """据窗口时长给个人话标签：5h 窗口 / 7天窗口 / 月度额度。"""
    if not window_seconds:
        return "窗口"
    if window_seconds <= 6 * 3600:
        return f"{round(window_seconds / 3600)}h 窗口"
    if window_seconds < 28 * 86400:
        return f"{round(window_seconds / 86400)}天窗口"
    return "月度额度"


def _target_args(config: Config):
    if config.feishu.chat_id:
        return ["--chat-id", config.feishu.chat_id]
    if config.feishu.user_id:
        return ["--user-id", config.feishu.user_id]
    return None


def _send(msg_type: str, content_json: str, config: Config) -> str:
    """统一的 lark-cli 发送（bot 身份）。三种消息共用，避免逻辑分叉。"""
    target_args = _target_args(config)
    if target_args is None:
        raise NotifyError("config.feishu 未配置 chat_id / user_id，无处可推")
    cmd = ["lark-cli", "im", "+messages-send", "--as", "bot",
           *target_args, "--msg-type", msg_type, "--content", content_json]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError) as e:
        raise NotifyError(f"调用 lark-cli 失败: {e}") from e
    if out.returncode != 0:
        raise NotifyError(f"lark-cli 退出码 {out.returncode}: {out.stderr.strip()[:200]}")
    return out.stdout.strip()


def _button(text: str, btn_type: str, value: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": btn_type,
        "width": "fill",
        "behaviors": [{"type": "callback", "value": value}],
    }


def _callback_value(action: str, **fields: Any) -> Dict[str, Any]:
    return {"cmd": "quota", "action": action, **fields}


# ---- 提醒卡（S3）---------------------------------------------------------

def build_card(usage: Usage, decision: Decision) -> Dict[str, Any]:
    five = usage.five_hour
    minutes = decision.minutes_to_reset
    minute_txt = f"{minutes:.0f}" if minutes is not None else "?"
    reset_local = five.resets_at.astimezone().strftime("%H:%M") if five.resets_at else "无"
    wl = window_label(five.window_seconds)

    summary = f"额度管家：{wl} {minute_txt} 分钟后重置"
    body_md = (
        f"**{wl}即将换挡**\n\n"
        f"- 已用额度：**{five.utilization:.0f}%**\n"
        f"- 距重置：**{minute_txt} 分钟**（{reset_local} 重置）\n\n"
        f"要现在预热下一个窗口吗？"
    )

    open_value = _callback_value(
        "warmup",
        resets_at=five.resets_at.isoformat() if five.resets_at else "",
    )
    skip_value = _callback_value(
        "skip",
        resets_at=five.resets_at.isoformat() if five.resets_at else "",
    )

    return {
        "schema": "2.0",
        "config": {"summary": {"content": summary}},
        "body": {
            "elements": [
                {"tag": "markdown", "content": body_md},
                {
                    "tag": "column_set",
                    "columns": [
                        {"tag": "column", "elements": [_button("🔥 开", "primary", open_value)]},
                        {"tag": "column", "elements": [_button("不开", "default", skip_value)]},
                    ],
                },
            ]
        },
    }


def push_card(usage: Usage, decision: Decision, config: Config,
              dry_run: bool = False) -> Optional[str]:
    """发提醒卡。dry_run 时只打印卡 JSON，不真发。"""
    card = build_card(usage, decision)
    if dry_run:
        print("[dry-run] 将发送卡片：")
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return None
    return _send("interactive", json.dumps(card, ensure_ascii=False), config)


# ---- 回执（S4）-----------------------------------------------------------

def push_receipt(text: str, config: Config, dry_run: bool = False) -> Optional[str]:
    """点「开」预热后往群里回一条纯文本结果（✅ 已开窗 / ❌ 失败）。"""
    if dry_run:
        print(f"[dry-run] 回执：{text}")
        return None
    content = json.dumps({"text": text}, ensure_ascii=False)
    return _send("text", content, config)


# ---- 状态卡（群聊主动查询）----------------------------------------------

StatusResult = Tuple[str, Optional[Usage], Optional[str]]  # (provider, usage, error)


def usage_bar(percent: float, width: int = 10) -> str:
    """Render a stable text progress bar for Feishu markdown cards."""
    if width <= 0:
        return ""
    value = max(0.0, min(float(percent), 100.0))
    filled = min(width, int(math.floor((value * width / 100.0) + 0.5)))
    return ("█" * filled) + ("░" * (width - filled))


def usage_status(percent: float) -> str:
    value = max(0.0, min(float(percent), 100.0))
    if value < 30:
        return "余量充足"
    if value < 70:
        return "正常使用"
    if value < 90:
        return "注意消耗"
    return "接近耗尽"


def _usage_error_advice(provider: str, error: str) -> str:
    detail = (error or "读取失败").strip()
    lowered = detail.lower()
    if provider == "cc" and any(key in lowered for key in ("token", "401", "过期", "expired")):
        return "运行一次 `claude` CLI 刷新登录"
    if provider == "codex" and any(key in lowered for key in ("token", "401", "auth")):
        return "运行一次 `codex` CLI 刷新登录"
    return "检查本机登录状态和网络后重试"


def build_status_card(results: Sequence[StatusResult]) -> Dict[str, Any]:
    """Build a compact visual work panel for all configured providers."""
    lines: List[str] = ["**额度状态**", ""]
    for name, usage, err in results:
        label = PROVIDER_LABEL.get(name, name)
        if usage is None:
            lines.extend([
                f"**{label}**",
                "░░░░░░░░░░ **?**",
                f"状态：**读取失败** · {err or '未知错误'}",
                f"建议：{_usage_error_advice(name, err or '')}",
                "",
            ])
            continue
        five = usage.five_hour
        reset_local = five.resets_at.astimezone().strftime("%m-%d %H:%M") if five.resets_at else "无"
        lines.extend([
            f"**{label}** · {window_label(five.window_seconds)}",
            f"{usage_bar(five.utilization)} **{five.utilization:.0f}%**",
            f"状态：**{usage_status(five.utilization)}**",
            f"恢复：**{reset_local}**",
        ])
        if usage.seven_day:
            sd = usage.seven_day
            lines.append(
                f"{window_label(sd.window_seconds)}："
                f"{usage_bar(sd.utilization)} **{sd.utilization:.0f}%**"
            )
        lines.append("")
    return {
        "schema": "2.0",
        "config": {"summary": {"content": "额度管家：当前状态"}},
        "body": {"elements": [{"tag": "markdown", "content": "\n".join(lines)}]},
    }


def push_status_card(results: Sequence[StatusResult], config: Config,
                     dry_run: bool = False) -> Optional[str]:
    card = build_status_card(results)
    if dry_run:
        print("[dry-run] 将发送状态卡：")
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return None
    return _send("interactive", json.dumps(card, ensure_ascii=False), config)


def build_oneup_card(
    provider: str,
    other_results: Sequence[StatusResult] = (),
    *,
    window_key: str = "",
) -> Dict[str, Any]:
    label = PROVIDER_LABEL.get(provider, provider)
    lines = [
        f"**{label} 已恢复，可以 one up 了**",
        "",
        f"当前可用：**{label}**",
        "建议：现在启动，保持连续工作。",
    ]
    for name, usage, err in other_results:
        other_label = PROVIDER_LABEL.get(name, name)
        if usage is None:
            lines.append(f"{other_label}：{err or '读取失败'}")
        else:
            lines.append(
                f"{other_label}：已用 {usage.five_hour.utilization:.0f}% · "
                f"{usage_status(usage.five_hour.utilization)}"
            )
    return {
        "schema": "2.0",
        "config": {"summary": {"content": f"{label} 已恢复，可以 one up"}},
        "body": {
            "elements": [
                {"tag": "markdown", "content": "\n".join(lines)},
                {
                    "tag": "column_set",
                    "columns": [
                        {
                            "tag": "column",
                            "elements": [
                                _button(
                                    "现在启动",
                                    "primary",
                                    _callback_value(
                                        "oneup_start",
                                        provider=provider,
                                        window_key=window_key,
                                    ),
                                )
                            ],
                        },
                        {
                            "tag": "column",
                            "elements": [
                                _button(
                                    "稍后提醒",
                                    "default",
                                    _callback_value(
                                        "oneup_snooze",
                                        minutes=30,
                                        provider=provider,
                                        window_key=window_key,
                                    ),
                                )
                            ],
                        },
                        {
                            "tag": "column",
                            "elements": [
                                _button(
                                    "今天不提醒",
                                    "default",
                                    _callback_value(
                                        "oneup_mute_today",
                                        provider=provider,
                                        window_key=window_key,
                                    ),
                                )
                            ],
                        },
                    ],
                },
            ]
        },
    }


def push_oneup_card(
    provider: str,
    other_results: Sequence[StatusResult],
    config: Config,
    dry_run: bool = False,
    *,
    window_key: str = "",
) -> Optional[str]:
    card = build_oneup_card(provider, other_results, window_key=window_key)
    if dry_run:
        print("[dry-run] 将发送 one-up 卡：")
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return None
    return _send("interactive", json.dumps(card, ensure_ascii=False), config)


# ---- V2 调度计划卡 ------------------------------------------------------

def build_schedule_scenario_card(
    target_date: date,
    preferences: SchedulePreferences,
    *,
    return_step: str,
    error: str = "",
) -> Dict[str, Any]:
    intro = [
        "**先认识一下你的日常使用场景**",
        "",
        "例如：独立开发产品、写小红书内容、调研 AI 工具。以后可以随时修改。",
    ]
    if error:
        intro.extend(["", f"⚠️ {error}"])
    field: Dict[str, Any] = {
        "tag": "input",
        "element_id": "daily_scenario_input",
        "name": "daily_scenario",
        "required": True,
        "width": "fill",
        "placeholder": {
            "tag": "plain_text",
            "content": "请输入你的日常 AI 使用场景",
        },
    }
    if preferences.daily_scenario:
        field["default_value"] = preferences.daily_scenario
    submit_value = flow_payload(
        step="scenario_saved",
        target_date=target_date,
        preferences=preferences,
        return_step=return_step,
    )
    return {
        "schema": "2.0",
        "config": {"summary": {"content": "Quota Butler：配置日常使用场景"}},
        "body": {
            "elements": [
                {"tag": "markdown", "content": "\n".join(intro)},
                {
                    "tag": "form",
                    "name": "schedule_scenario_form",
                    "direction": "vertical",
                    "elements": [
                        field,
                        {
                            "tag": "button",
                            "name": "submit_schedule_scenario",
                            "text": {"tag": "plain_text", "content": "保存并继续"},
                            "type": "primary",
                            "form_action_type": "submit",
                            "behaviors": [{"type": "callback", "value": submit_value}],
                        },
                    ],
                },
            ]
        },
    }


def build_schedule_task_card(
    target_date: date,
    preferences: SchedulePreferences = SchedulePreferences(),
) -> Dict[str, Any]:
    choices = [
        ("编码开发", "coding"),
        ("内容创作", "content"),
        ("调研分析", "research"),
        ("混合任务", "mixed"),
    ]
    buttons = [
        _button(
            label,
            "primary" if value == preferences.task_type else "default",
            flow_payload(
                step="intensity",
                target_date=target_date,
                preferences=replace(preferences, task_type=value),
            ),
        )
        for label, value in choices
    ]
    return _guided_choice_card(
        "明天主要做什么？",
        "先选择主要任务类型，计划会据此解释每次接力的目的。",
        buttons,
    )


def build_schedule_intensity_card(
    target_date: date,
    preferences: SchedulePreferences,
) -> Dict[str, Any]:
    choices = [
        ("轻量", "light"),
        ("正常", "normal"),
        ("高强度", "high"),
    ]
    buttons = [
        _button(
            label,
            "primary" if value == preferences.intensity else "default",
            flow_payload(
                step="time",
                target_date=target_date,
                preferences=replace(preferences, intensity=value),
            ),
        )
        for label, value in choices
    ]
    return _guided_choice_card(
        "希望保持什么工作强度？",
        "强度会影响接力频率；轻量模式会尽量减少打断。",
        buttons,
    )


def build_schedule_time_card(
    target_date: date,
    preferences: SchedulePreferences,
    *,
    error: str = "",
) -> Dict[str, Any]:
    intro = [
        "**选择工作时间**",
        "",
        "时区：**北京时间（Asia/Shanghai）**",
    ]
    if error:
        intro.extend(["", f"⚠️ {error}"])
    submit_value = flow_payload(
        step="summary",
        target_date=target_date,
        preferences=preferences,
    )
    form = {
        "tag": "form",
        "name": "schedule_time_form",
        "direction": "vertical",
        "elements": [
            {"tag": "markdown", "content": "**开始时间**"},
            {
                "tag": "picker_time",
                "element_id": "work_start_picker",
                "name": "work_start",
                "required": True,
                "initial_time": preferences.work_start,
                "width": "fill",
            },
            {"tag": "markdown", "content": "**结束时间**"},
            {
                "tag": "picker_time",
                "element_id": "work_end_picker",
                "name": "work_end",
                "required": True,
                "initial_time": preferences.work_end,
                "width": "fill",
            },
            {
                "tag": "button",
                "name": "submit_schedule_time",
                "text": {"tag": "plain_text", "content": "确认时间"},
                "type": "primary",
                "form_action_type": "submit",
                "behaviors": [{"type": "callback", "value": submit_value}],
            },
        ],
    }
    return {
        "schema": "2.0",
        "config": {"summary": {"content": "Quota Butler：选择工作时间"}},
        "body": {
            "elements": [
                {"tag": "markdown", "content": "\n".join(intro)},
                form,
            ]
        },
    }


def build_schedule_summary_card(
    target_date: date,
    preferences: SchedulePreferences,
) -> Dict[str, Any]:
    scenario_line = (
        f"- 日常场景：**{_markdown_inline(preferences.daily_scenario)}**"
        if preferences.daily_scenario else
        "- 日常场景：**尚未设置**"
    )
    markdown = "\n".join([
        "**确认明天的安排偏好**",
        "",
        scenario_line,
        f"- 主要任务：**{TASK_TYPE_LABELS[preferences.task_type]}**",
        f"- 工作强度：**{INTENSITY_LABELS[preferences.intensity]}**",
        f"- 工作时间：**{preferences.work_start}–{preferences.work_end}**",
        "- 时区：**北京时间（Asia/Shanghai）**",
    ])
    actions = [
        ("生成计划", "primary", "generate"),
        ("修改任务", "default", "task"),
        ("修改强度", "default", "intensity"),
        ("修改时间", "default", "time"),
        ("修改场景", "default", "scenario"),
    ]
    return {
        "schema": "2.0",
        "config": {"summary": {"content": "Quota Butler：确认规划偏好"}},
        "body": {
            "elements": [
                {"tag": "markdown", "content": markdown},
                *_button_grid([
                    _button(
                        label,
                        button_type,
                        flow_payload(
                            step=step,
                            target_date=target_date,
                            preferences=preferences,
                        ),
                    )
                    for label, button_type, step in actions
                ]),
            ]
        },
    }


def push_guided_schedule_card(
    card: Dict[str, Any],
    config: Config,
    dry_run: bool = False,
) -> Optional[str]:
    if dry_run:
        print("[dry-run] 将发送规划引导卡：")
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return None
    return _send("interactive", json.dumps(card, ensure_ascii=False), config)


def _guided_choice_card(
    title: str,
    description: str,
    buttons: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "schema": "2.0",
        "config": {"summary": {"content": f"Quota Butler：{title}"}},
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": f"**{title}**\n\n{description}",
                },
                *_button_grid(buttons),
            ]
        },
    }


def _button_grid(
    buttons: Sequence[Dict[str, Any]],
    *,
    columns: int = 2,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for start in range(0, len(buttons), columns):
        rows.append({
            "tag": "column_set",
            "columns": [
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "elements": [button],
                }
                for button in buttons[start:start + columns]
            ],
        })
    return rows


def build_schedule_card(
    plan: SchedulePlan,
    *,
    warnings: Sequence[str] = (),
) -> Dict[str, Any]:
    del warnings
    preferences = plan.preferences or SchedulePreferences(
        task_type="mixed",
        intensity={
            "savings": "light",
            "balanced": "normal",
            "sustain": "high",
        }.get(plan.mode, "normal"),
        work_start=_fmt_time(plan.work_start),
        work_end=_fmt_time(plan.work_end),
    )
    agent_labels = " + ".join(
        PROVIDER_LABEL.get(agent, agent) for agent in plan.agents
    )
    lines: List[str] = [
        "**明日 AI 工作计划**",
        "",
        (
            f"{preferences.work_start}–{preferences.work_end}，"
            f"共安排 **{plan.relay_count} 次接力**。"
        ),
        f"- 本次使用：**{agent_labels}**",
        (
            f"- 日常场景：**{_markdown_inline(preferences.daily_scenario)}**"
            if preferences.daily_scenario else
            "- 日常场景：**未设置**"
        ),
        f"- 主要任务：**{TASK_TYPE_LABELS[preferences.task_type]}**",
        f"- 工作强度：**{INTENSITY_LABELS[preferences.intensity]}**",
        "",
        "**计算依据**",
        f"- 计划覆盖率：**{plan.cas * 100:.0f}%**",
        f"- 预计空档：**{plan.waiting_minutes:.0f} 分钟**",
        f"- 预计接力：**{plan.relay_count} 次**",
        "- 按当前额度窗口估算，实际恢复时间可能变化。",
        "",
        "**为什么这样安排**",
    ]
    lines.extend(_guided_timeline(plan, preferences))

    adopt_value = _callback_value("adopt_schedule", plan=plan_record(plan))
    adjust_value = flow_payload(
        step="summary",
        target_date=plan.work_start.date(),
        preferences=preferences,
    )
    remind_value = _callback_value("schedule_remind_only")

    return {
        "schema": "2.0",
        "config": {"summary": {"content": "Quota Butler：AI Agent 调度计划"}},
        "body": {
            "elements": [
                {"tag": "markdown", "content": "\n".join(lines)},
                *_button_grid([
                    _button("采用计划", "primary", adopt_value),
                    _button("调整设置", "default", adjust_value),
                    _button("仅提醒", "default", remind_value),
                ]),
            ]
        },
    }


def _guided_timeline(
    plan: SchedulePlan,
    preferences: SchedulePreferences,
) -> List[str]:
    lines = [
        (
            f"- {preferences.work_start} · 开始"
            f"{TASK_TYPE_LABELS[preferences.task_type]}，先完成最需要连续注意力的部分"
        )
    ]
    for event in plan.events:
        if event.kind == "warmup":
            label = PROVIDER_LABEL.get(event.agent, event.agent)
            if event.agent == "codex":
                lines.append(
                    f"- {_fmt_time(event.at)} · **{label}** 提前预热，减少开工等待"
                )
            else:
                lines.append(
                    f"- {_fmt_time(event.at)} · **{label}** 预计可用，作为接力候选"
                )
        elif event.kind == "recovery" and plan.work_start <= event.at <= plan.work_end:
            label = PROVIDER_LABEL.get(event.agent, event.agent)
            lines.append(
                f"- {_fmt_time(event.at)} · **{label}** 预计恢复，可承接下一段工作"
            )
    for relay in plan.relay_points:
        lines.append(f"- {_fmt_time(relay.at)} · {relay.note}")
    if not plan.relay_points:
        lines.append("- 中途不安排固定接力点，尽量减少打断")
    lines.sort(key=_timeline_sort_key)
    return lines


def _timeline_sort_key(line: str) -> str:
    marker = line.removeprefix("- ") if hasattr(str, "removeprefix") else line[2:]
    return marker[:5]


def _markdown_inline(value: str) -> str:
    text = str(value).replace("\\", "\\\\")
    for char in ("*", "_", "~", "`", "[", "]", "(", ")"):
        text = text.replace(char, f"\\{char}")
    return text


def push_schedule_card(
    plan: SchedulePlan,
    config: Config,
    dry_run: bool = False,
    *,
    warnings: Sequence[str] = (),
) -> Optional[str]:
    card = build_schedule_card(plan, warnings=warnings)
    if dry_run:
        print("[dry-run] 将发送调度计划卡：")
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return None
    return _send("interactive", json.dumps(card, ensure_ascii=False), config)


def build_active_plan_card(record: Dict[str, Any]) -> Dict[str, Any]:
    start = _parse_iso_time(record.get("work_start"))
    end = _parse_iso_time(record.get("work_end"))
    lines = [
        "**当前生效计划**",
        "",
        f"- 模式：**{MODE_LABEL.get(str(record.get('mode')), record.get('mode', '未知'))}**",
        f"- 工作时间：**{start} - {end}**",
        f"- Plan ID：`{record.get('plan_id', '')}`",
        "",
        "**预热任务**",
    ]
    tasks = record.get("tasks") or []
    if not tasks:
        lines.append("- 没有待执行任务")
    for task in tasks:
        provider = PROVIDER_LABEL.get(str(task.get("provider")), str(task.get("provider")))
        status = "已执行" if task.get("status") == "executed" else "待执行"
        lines.append(
            f"- {_parse_iso_time(task.get('scheduled_for'))} · **{provider}** · {status}"
        )
    return {
        "schema": "2.0",
        "config": {"summary": {"content": "Quota Butler：当前生效计划"}},
        "body": {
            "elements": [
                {"tag": "markdown", "content": "\n".join(lines)},
                {
                    "tag": "column_set",
                    "columns": [
                        {
                            "tag": "column",
                            "elements": [
                                _button(
                                    "取消计划",
                                    "danger",
                                    _callback_value("cancel_schedule"),
                                )
                            ],
                        }
                    ],
                },
            ]
        },
    }


def push_active_plan_card(record: Dict[str, Any], config: Config,
                          dry_run: bool = False) -> Optional[str]:
    card = build_active_plan_card(record)
    if dry_run:
        print("[dry-run] 将发送生效计划卡：")
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return None
    return _send("interactive", json.dumps(card, ensure_ascii=False), config)


def build_command_menu_card() -> Dict[str, Any]:
    def action_button(text: str, action: str, value: Dict[str, Any], btn_type: str = "default"):
        payload = _callback_value(action, **value)
        return _button(text, btn_type, payload)

    return {
        "schema": "2.0",
        "config": {"summary": {"content": "Quota Butler：测试菜单"}},
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": (
                        "**Quota Butler 测试菜单**\n\n"
                        "选择一个动作。采用计划后会创建本地 launchd 预热任务。"
                    ),
                },
                {
                    "tag": "column_set",
                    "columns": [
                        {
                            "tag": "column",
                            "elements": [
                                action_button(
                                    "帮我安排明天",
                                    "schedule_intent",
                                    {"intent": "帮我安排明天"},
                                    "primary",
                                )
                            ],
                        },
                        {
                            "tag": "column",
                            "elements": [
                                action_button("今天冲刺", "schedule_intent", {"intent": "今天冲刺"})
                            ],
                        },
                    ],
                },
                {
                    "tag": "column_set",
                    "columns": [
                        {
                            "tag": "column",
                            "elements": [
                                action_button("不断粮模式", "schedule_intent", {"intent": "不断粮模式"})
                            ],
                        },
                        {
                            "tag": "column",
                            "elements": [
                                action_button("当前额度", "query_status", {})
                            ],
                        },
                        {
                            "tag": "column",
                            "elements": [
                                action_button("查看计划", "view_schedule", {})
                            ],
                        },
                    ],
                },
            ]
        },
    }


def push_command_menu_card(config: Config, dry_run: bool = False) -> Optional[str]:
    card = build_command_menu_card()
    if dry_run:
        print("[dry-run] 将发送测试菜单卡：")
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return None
    return _send("interactive", json.dumps(card, ensure_ascii=False), config)


def _fmt_time(value) -> str:
    return value.astimezone().strftime("%H:%M") if getattr(value, "tzinfo", None) else value.strftime("%H:%M")


def _parse_iso_time(value: object) -> str:
    try:
        return datetime.fromisoformat(str(value)).astimezone().strftime("%m-%d %H:%M")
    except (TypeError, ValueError):
        return "未知"


def _collaboration_timeline(plan: SchedulePlan) -> List[str]:
    lines: List[str] = []
    warmups = [event for event in plan.events if event.kind == "warmup"]
    recoveries = [
        event for event in plan.events
        if event.kind == "recovery" and plan.work_start <= event.at <= plan.work_end
    ]

    for event in warmups:
        label = PROVIDER_LABEL.get(event.agent, event.agent)
        lines.append(f"- {_fmt_time(event.at)} · **{label}** 预热")

    lines.append(f"- {_fmt_time(plan.work_start)} · **你开始创作**")
    cursor = plan.work_start
    for index, event in enumerate(recoveries):
        if event.at > cursor:
            role = "你主导：需求整理 / 编码 / 验证" if index == 0 else "Agent 接力：重构 / 测试 / 文档"
            lines.append(f"- {_fmt_time(cursor)}-{_fmt_time(event.at)} · {role}")
        label = PROVIDER_LABEL.get(event.agent, event.agent)
        lines.append(f"- {_fmt_time(event.at)} · **{label}** 恢复，可接力")
        cursor = event.at

    if cursor < plan.work_end:
        role = "深度创作：自由发挥 / 联调 / 收尾" if recoveries else "你主导：深度创作 / 验证"
        lines.append(f"- {_fmt_time(cursor)}-{_fmt_time(plan.work_end)} · {role}")
    return lines
