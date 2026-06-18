import unittest
from datetime import date, datetime, timedelta, timezone

from quota_butler.notify import (
    build_active_plan_card,
    build_card,
    build_command_menu_card,
    build_oneup_card,
    build_schedule_intensity_card,
    build_schedule_scenario_card,
    build_schedule_summary_card,
    build_schedule_task_card,
    build_schedule_time_card,
    build_schedule_card,
    build_status_card,
    usage_bar,
    usage_status,
)
from quota_butler.planner import build_plan, plan_from_preferences
from quota_butler.providers.base import Usage, WindowUsage
from quota_butler.rules import Decision
from quota_butler.schedule_flow import SchedulePreferences


def _card_markdown(card):
    return "\n".join(
        element.get("content", "")
        for element in card["body"]["elements"]
        if element.get("tag") == "markdown"
    )


def _callback_values(node):
    values = []
    if isinstance(node, dict):
        if node.get("type") == "callback" and isinstance(node.get("value"), dict):
            values.append(node["value"])
        for value in node.values():
            values.extend(_callback_values(value))
    elif isinstance(node, list):
        for value in node:
            values.extend(_callback_values(value))
    return values


class TestStatusCard(unittest.TestCase):
    def test_usage_bar_clamps_and_rounds_to_ten_cells(self):
        self.assertEqual(usage_bar(-1), "░░░░░░░░░░")
        self.assertEqual(usage_bar(63), "██████░░░░")
        self.assertEqual(usage_bar(100), "██████████")
        self.assertEqual(usage_bar(120), "██████████")

    def test_usage_status_uses_product_thresholds(self):
        self.assertEqual(usage_status(0), "余量充足")
        self.assertEqual(usage_status(30), "正常使用")
        self.assertEqual(usage_status(70), "注意消耗")
        self.assertEqual(usage_status(90), "接近耗尽")

    def test_status_card_renders_each_agent_as_visual_block(self):
        usage = Usage(
            provider="codex",
            five_hour=WindowUsage(
                utilization=63,
                resets_at=datetime(2026, 6, 18, 14, 30, tzinfo=timezone.utc),
                window_seconds=5 * 3600,
            ),
        )
        card = build_status_card([
            ("codex", usage, None),
            ("cc", None, "token 已过期"),
        ])
        markdown = _card_markdown(card)

        self.assertIn("██████░░░░ **63%**", markdown)
        self.assertIn("状态：**正常使用**", markdown)
        self.assertIn("Claude Code", markdown)
        self.assertIn("运行一次 `claude` CLI 刷新登录", markdown)

    def test_oneup_card_has_start_snooze_and_mute_actions(self):
        card = build_oneup_card(
            "codex",
            [("cc", None, "token 已过期")],
            window_key="codex:2026-06-18T09:59:00+00:00",
        )
        markdown = _card_markdown(card)
        self.assertIn("Codex 已恢复", markdown)
        actions = [
            column["elements"][0]["behaviors"][0]["value"]["action"]
            for row in card["body"]["elements"]
            if row.get("tag") == "column_set"
            for column in row["columns"]
        ]
        self.assertEqual(actions, ["oneup_start", "oneup_snooze", "oneup_mute_today"])
        snooze = card["body"]["elements"][1]["columns"][1]["elements"][0]
        self.assertEqual(
            snooze["behaviors"][0]["value"]["window_key"],
            "codex:2026-06-18T09:59:00+00:00",
        )
        start = card["body"]["elements"][1]["columns"][0]["elements"][0]
        self.assertEqual(
            start["behaviors"][0]["value"]["window_key"],
            "codex:2026-06-18T09:59:00+00:00",
        )

    def test_all_interactive_cards_use_private_quota_command_protocol(self):
        usage = Usage(
            provider="cc",
            five_hour=WindowUsage(
                utilization=50,
                resets_at=datetime(2026, 6, 18, 12, 0, tzinfo=timezone.utc),
                window_seconds=5 * 3600,
            ),
        )
        start = datetime(2026, 6, 18, 9, 0, tzinfo=timezone.utc)
        plan = build_plan(
            mode="balanced",
            agents=("cc", "codex"),
            work_start=start,
            work_end=start + timedelta(hours=8),
        )
        cards = [
            build_card(usage, Decision(True, "test", 10)),
            build_oneup_card("codex", window_key="w1"),
            build_schedule_card(plan),
            build_active_plan_card(
                {
                    "plan_id": "p1",
                    "mode": "balanced",
                    "work_start": "2026-06-18T09:00:00+00:00",
                    "work_end": "2026-06-18T17:00:00+00:00",
                    "tasks": [],
                }
            ),
            build_command_menu_card(),
        ]

        values = [value for card in cards for value in _callback_values(card)]
        self.assertTrue(values)
        self.assertTrue(all(value.get("cmd") == "quota" for value in values))
        self.assertTrue(all("__claude_cb" not in value for value in values))


class TestScheduleCard(unittest.TestCase):
    def test_task_card_uses_two_by_two_mobile_button_grid(self):
        card = build_schedule_task_card(date(2026, 6, 19))
        rows = [
            element for element in card["body"]["elements"]
            if element.get("tag") == "column_set"
        ]

        self.assertEqual([len(row["columns"]) for row in rows], [2, 2])
        buttons = [
            column["elements"][0]
            for row in rows
            for column in row["columns"]
        ]
        self.assertTrue(all(button["width"] == "fill" for button in buttons))

    def test_task_card_buttons_preserve_context_and_advance_to_intensity(self):
        target = date(2026, 6, 19)
        card = build_schedule_task_card(target)
        values = _callback_values(card)

        self.assertEqual(len(values), 4)
        self.assertEqual(
            {value["preferences"]["task_type"] for value in values},
            {"coding", "content", "research", "mixed"},
        )
        self.assertTrue(all(value["step"] == "intensity" for value in values))
        self.assertTrue(all(value["target_date"] == "2026-06-19" for value in values))

    def test_intensity_card_buttons_preserve_task_and_advance_to_time(self):
        prefs = SchedulePreferences(task_type="coding")
        card = build_schedule_intensity_card(date(2026, 6, 19), prefs)
        values = _callback_values(card)

        self.assertEqual(len(values), 3)
        self.assertEqual(
            {value["preferences"]["intensity"] for value in values},
            {"light", "normal", "high"},
        )
        self.assertTrue(
            all(value["preferences"]["task_type"] == "coding" for value in values)
        )
        self.assertTrue(all(value["step"] == "time" for value in values))

    def test_choice_and_summary_actions_never_exceed_two_columns_per_row(self):
        prefs = SchedulePreferences(task_type="coding", daily_scenario="独立开发产品")
        cards = [
            build_schedule_intensity_card(date(2026, 6, 19), prefs),
            build_schedule_summary_card(date(2026, 6, 19), prefs),
        ]

        for card in cards:
            rows = [
                element for element in card["body"]["elements"]
                if element.get("tag") == "column_set"
            ]
            self.assertTrue(rows)
            self.assertTrue(all(len(row["columns"]) <= 2 for row in rows))

    def test_first_use_scenario_card_accepts_manual_input(self):
        card = build_schedule_scenario_card(
            date(2026, 6, 19),
            SchedulePreferences(),
            return_step="task",
        )
        form = next(
            element for element in card["body"]["elements"]
            if element.get("tag") == "form"
        )
        field = next(
            element for element in form["elements"]
            if element.get("tag") == "input"
        )
        submit = form["elements"][-1]

        self.assertEqual(field["name"], "daily_scenario")
        self.assertTrue(field["required"])
        self.assertEqual(submit["form_action_type"], "submit")
        value = submit["behaviors"][0]["value"]
        self.assertEqual(value["step"], "scenario_saved")
        self.assertEqual(value["return_step"], "task")

    def test_time_card_uses_required_native_time_pickers_in_a_form(self):
        prefs = SchedulePreferences(
            task_type="research",
            intensity="high",
            work_start="10:00",
            work_end="18:00",
        )
        card = build_schedule_time_card(date(2026, 6, 19), prefs)
        form = next(
            element for element in card["body"]["elements"]
            if element.get("tag") == "form"
        )
        pickers = [
            element for element in form["elements"]
            if element.get("tag") == "picker_time"
        ]

        self.assertEqual([picker["name"] for picker in pickers], [
            "work_start",
            "work_end",
        ])
        self.assertEqual([picker["initial_time"] for picker in pickers], [
            "10:00",
            "18:00",
        ])
        self.assertTrue(all(picker["required"] for picker in pickers))
        submit = form["elements"][-1]
        self.assertEqual(submit["form_action_type"], "submit")
        value = submit["behaviors"][0]["value"]
        self.assertEqual(value["step"], "summary")
        self.assertEqual(value["preferences"]["task_type"], "research")

    def test_summary_card_shows_beijing_time_and_edit_actions(self):
        prefs = SchedulePreferences(
            task_type="content",
            intensity="light",
            work_start="10:00",
            work_end="16:00",
            daily_scenario="独立开发产品",
        )
        card = build_schedule_summary_card(date(2026, 6, 19), prefs)
        markdown = _card_markdown(card)
        values = _callback_values(card)

        self.assertIn("内容创作", markdown)
        self.assertIn("轻量", markdown)
        self.assertIn("10:00–16:00", markdown)
        self.assertIn("北京时间", markdown)
        self.assertIn("独立开发产品", markdown)
        self.assertEqual(
            [value["step"] for value in values],
            ["generate", "task", "intensity", "time", "scenario"],
        )

    def test_manual_scenario_is_escaped_before_markdown_rendering(self):
        prefs = SchedulePreferences(daily_scenario="**伪标题** [链接](x)")

        markdown = _card_markdown(
            build_schedule_summary_card(date(2026, 6, 19), prefs)
        )

        self.assertNotIn("日常场景：****伪标题****", markdown)
        self.assertIn(r"\*\*伪标题\*\*", markdown)
        self.assertIn(r"\[链接\]\(x\)", markdown)

    def test_schedule_card_is_human_readable_and_shows_trust_metrics(self):
        plan = plan_from_preferences(
            SchedulePreferences(
                task_type="coding",
                intensity="normal",
                work_start="09:00",
                work_end="17:00",
                daily_scenario="独立开发产品",
            ),
            target_date=date(2026, 6, 19),
            agents=("codex",),
        )
        card = build_schedule_card(plan)
        markdown = _card_markdown(card)

        self.assertIn("09:00–17:00", markdown)
        self.assertIn("本次使用：**Codex**", markdown)
        self.assertIn("日常场景：**独立开发产品**", markdown)
        self.assertNotIn("Claude Code", markdown)
        self.assertIn("计划覆盖率：**100%**", markdown)
        self.assertIn("预计空档：**0 分钟**", markdown)
        self.assertIn("预计接力：**1 次**", markdown)
        self.assertIn("代码实现与测试", markdown)
        self.assertIn("按当前额度窗口估算", markdown)
        self.assertNotIn("CAS", markdown)

    def test_schedule_card_has_exactly_three_required_actions(self):
        plan = plan_from_preferences(
            SchedulePreferences(task_type="content", intensity="high"),
            target_date=date(2026, 6, 19),
            agents=("codex",),
        )
        card = build_schedule_card(plan)
        values = _callback_values(card)

        self.assertEqual(
            [value["action"] for value in values],
            ["adopt_schedule", "schedule_flow", "schedule_remind_only"],
        )
        adjust = values[1]
        self.assertEqual(adjust["step"], "summary")
        self.assertEqual(adjust["preferences"]["task_type"], "content")
        self.assertEqual(values[0]["plan"]["plan_version"], 2)
        rows = [
            element for element in card["body"]["elements"]
            if element.get("tag") == "column_set"
        ]
        self.assertEqual([len(row["columns"]) for row in rows], [2, 1])

    def test_schedule_card_never_surfaces_unavailable_agent_warning(self):
        plan = plan_from_preferences(
            SchedulePreferences(),
            target_date=date(2026, 6, 19),
            agents=("codex",),
        )
        card = build_schedule_card(
            plan,
            warnings=("Claude Code 不可用：token 已过期",),
        )

        self.assertNotIn("Claude Code 不可用", _card_markdown(card))

    def test_schedule_card_does_not_claim_claude_will_be_preheated(self):
        plan = plan_from_preferences(
            SchedulePreferences(),
            target_date=date(2026, 6, 19),
            agents=("cc", "codex"),
        )

        markdown = _card_markdown(build_schedule_card(plan))

        self.assertNotIn("**Claude Code** 提前准备", markdown)
        self.assertIn("**Claude Code** 预计可用，作为接力候选", markdown)
        self.assertIn("**Codex** 提前预热", markdown)

    def test_active_plan_card_lists_pending_tasks_and_cancel_action(self):
        card = build_active_plan_card({
            "plan_id": "p1",
            "mode": "balanced",
            "work_start": "2026-06-19T09:00:00+00:00",
            "work_end": "2026-06-19T17:00:00+00:00",
            "tasks": [
                {
                    "provider": "codex",
                    "scheduled_for": "2026-06-19T08:30:00+00:00",
                    "status": "pending",
                }
            ],
        })
        markdown = _card_markdown(card)
        self.assertIn("当前生效计划", markdown)
        self.assertIn("Codex", markdown)
        button = card["body"]["elements"][-1]["columns"][0]["elements"][0]
        self.assertEqual(button["behaviors"][0]["value"]["action"], "cancel_schedule")

    def test_command_menu_exposes_active_plan(self):
        card = build_command_menu_card()
        actions = [
            column["elements"][0]["behaviors"][0]["value"]["action"]
            for row in card["body"]["elements"]
            if row.get("tag") == "column_set"
            for column in row["columns"]
        ]
        self.assertIn("view_schedule", actions)


if __name__ == "__main__":
    unittest.main()
