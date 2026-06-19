import unittest
from datetime import date, datetime, timezone

from quota_butler.agent_status import AgentState, AgentStatus
from quota_butler.notify import (
    build_active_plan_card,
    build_agent_control_card,
    build_bedtime_card,
    build_command_menu_card,
    build_recovery_card,
    build_schedule_card,
    build_time_card,
    build_time_mode_card,
    build_status_card,
    usage_bar,
)
from quota_butler.plan_tasks import plan_record
from quota_butler.planner import build_plan
from quota_butler.providers.base import Usage, WindowUsage
from quota_butler.schedule_flow import PlanRequest


def _markdown(card):
    return "\n".join(
        element.get("content", "")
        for element in card["body"]["elements"]
        if element.get("tag") == "markdown"
    )


def _callbacks(node):
    values = []
    if isinstance(node, dict):
        if node.get("type") == "callback" and isinstance(node.get("value"), dict):
            values.append(node["value"])
        for child in node.values():
            values.extend(_callbacks(child))
    elif isinstance(node, list):
        for child in node:
            values.extend(_callbacks(child))
    return values


class TestStatusCard(unittest.TestCase):
    def test_progress_bar_is_clamped(self):
        self.assertEqual(usage_bar(-1), "░░░░░░░░░░")
        self.assertEqual(usage_bar(63), "██████░░░░")
        self.assertEqual(usage_bar(100), "██████████")

    def test_status_card_distinguishes_all_agent_states(self):
        statuses = {
            "cc": AgentStatus(
                "cc",
                AgentState.CONNECTED,
                usage=_usage("cc", 63),
            ),
            "codex": AgentStatus(
                "codex",
                AgentState.UNAVAILABLE,
                executable="/usr/local/bin/codex",
                detail="wham/usage HTTP 503",
            ),
        }

        text = _markdown(build_status_card(statuses))

        self.assertIn("Claude Code", text)
        self.assertIn("██████░░░░ **63%**", text)
        self.assertIn("Codex", text)
        self.assertIn("暂时无法读取", text)
        self.assertNotIn("未检测到安装", text)


class TestPlanningCards(unittest.TestCase):
    def setUp(self):
        self.request = PlanRequest(
            date(2026, 6, 20),
            "point",
            "09:00",
            "14:00",
            "auto",
        )
        self.plan = build_plan(self.request, {"cc": _usage("cc", 30)})

    def test_time_mode_card_only_asks_point_or_range(self):
        card = build_time_mode_card(date(2026, 6, 20))
        text = _markdown(card)
        actions = [value["step"] for value in _callbacks(card)]

        self.assertIn("重度使用时间", text)
        self.assertEqual(actions, ["edit_time_point", "edit_time_range"])
        self.assertNotIn("任务类型", text)
        self.assertNotIn("工作强度", text)

    def test_point_time_card_uses_one_native_picker(self):
        card = build_time_card(self.request)
        form = next(e for e in card["body"]["elements"] if e.get("tag") == "form")
        pickers = [e for e in form["elements"] if e.get("tag") == "picker_time"]

        self.assertEqual([picker["name"] for picker in pickers], ["work_start"])
        self.assertEqual(pickers[0]["initial_time"], "09:00")
        submit = form["elements"][-1]
        self.assertEqual(submit["behaviors"][0]["value"]["step"], "generate_plan")

    def test_range_time_card_uses_start_and_end_pickers(self):
        request = PlanRequest(date(2026, 6, 20), "range", "09:00", "18:00", "auto")
        card = build_time_card(request)
        form = next(e for e in card["body"]["elements"] if e.get("tag") == "form")
        pickers = [e for e in form["elements"] if e.get("tag") == "picker_time"]

        self.assertEqual(
            [picker["name"] for picker in pickers],
            ["work_start", "work_end"],
        )

    def test_plan_card_shows_exact_warmups_and_adjustment_buttons(self):
        card = build_schedule_card(self.plan)
        text = _markdown(card)
        actions = [value["action"] for value in _callbacks(card)]

        self.assertIn("09:00–14:00", text)
        self.assertIn("06:30", text)
        self.assertIn("11:30", text)
        self.assertIn("准备第一个窗口", text)
        self.assertIn("恢复后准备第二个窗口", text)
        self.assertIn("预计连续覆盖：**09:00–14:00**", text)
        self.assertNotIn("CAS", text)
        self.assertEqual(
            actions,
            [
                "adopt_schedule",
                "adjust_schedule_agents",
                "adjust_schedule_time",
                "schedule_remind_only",
            ],
        )
        self.assertIn("更换 AI 工具", str(card))
        self.assertNotIn("调整 Agent", str(card))

    def test_dual_agent_control_exposes_three_explicit_tool_choices(self):
        card = build_agent_control_card(
            self.request,
            {
                "cc": AgentStatus("cc", AgentState.CONNECTED, usage=_usage("cc", 20)),
                "codex": AgentStatus(
                    "codex", AgentState.CONNECTED, usage=_usage("codex", 30)
                ),
            },
        )
        strategies = [
            value["agent_strategy"]
            for value in _callbacks(card)
            if value.get("step") == "generate_plan"
        ]

        text = str(card)
        self.assertEqual(strategies, ["cc", "codex", "both"])
        self.assertIn("两个都用", text)
        self.assertNotIn("自动安排", text)

    def test_single_agent_control_has_no_meaningless_selector(self):
        card = build_agent_control_card(
            self.request,
            {"cc": AgentStatus("cc", AgentState.CONNECTED, usage=_usage("cc", 20))},
        )
        text = _markdown(card)
        callbacks = _callbacks(card)

        self.assertIn("当前仅检测到 Claude Code", text)
        self.assertIn("AI 工具", str(card))
        self.assertEqual([value["action"] for value in callbacks], ["redetect_agents"])

    def test_active_plan_card_shows_pending_nodes_and_cancel(self):
        record = plan_record(self.plan)
        record["status"] = "active"
        record["tasks"] = [{"provider": "cc", "scheduled_for": record["events"][0]["at"]}]

        card = build_active_plan_card(record)

        self.assertIn("06:30", _markdown(card))
        self.assertEqual(_callbacks(card)[0]["action"], "cancel_schedule")


class TestReminderAndMenuCards(unittest.TestCase):
    def test_recovery_card_has_direct_warmup_snooze_and_skip(self):
        card = build_recovery_card("cc", "window-1")
        actions = [value["action"] for value in _callbacks(card)]
        self.assertEqual(actions, ["warmup_now", "recovery_snooze", "recovery_skip"])

    def test_bedtime_card_asks_only_if_tomorrow_is_heavy(self):
        card = build_bedtime_card()
        text = _markdown(card)
        actions = [value["action"] for value in _callbacks(card)]
        self.assertIn("明天有重度使用 AI 的计划吗", text)
        self.assertEqual(actions, ["schedule_intent", "tomorrow_skip"])

    def test_menu_only_keeps_three_v3_entries(self):
        card = build_command_menu_card()
        actions = [value["action"] for value in _callbacks(card)]
        self.assertEqual(actions, ["query_status", "schedule_intent", "view_schedule"])

    def test_all_callbacks_use_private_quota_command(self):
        cards = [
            build_recovery_card("codex", "w1"),
            build_bedtime_card(),
            build_time_mode_card(date(2026, 6, 20)),
            build_schedule_card(self.plan if hasattr(self, "plan") else build_plan(
                PlanRequest(date(2026, 6, 20), "point", "09:00", "14:00"),
                {"cc": _usage("cc", 20)},
            )),
            build_command_menu_card(),
        ]
        self.assertTrue(
            all(value.get("cmd") == "quota" for card in cards for value in _callbacks(card))
        )


def _usage(provider, utilization):
    return Usage(
        provider,
        WindowUsage(
            utilization,
            datetime(2026, 6, 20, 11, 30, tzinfo=timezone.utc),
            5 * 3600,
        ),
    )


if __name__ == "__main__":
    unittest.main()
