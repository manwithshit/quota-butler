import unittest
from datetime import datetime, timedelta, timezone

from quota_butler.notify import (
    build_active_plan_card,
    build_card,
    build_command_menu_card,
    build_oneup_card,
    build_schedule_card,
    build_status_card,
    usage_bar,
    usage_status,
)
from quota_butler.planner import build_plan
from quota_butler.providers.base import Usage, WindowUsage
from quota_butler.rules import Decision


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
    def test_schedule_card_renders_human_agent_collaboration_timeline(self):
        start = datetime(2026, 6, 18, 9, 0, tzinfo=timezone.utc)
        plan = build_plan(
            mode="balanced",
            agents=("cc", "codex"),
            work_start=start,
            work_end=start + timedelta(hours=8),
        )
        card = build_schedule_card(plan)
        markdown = _card_markdown(card)

        self.assertIn("协作时间线", markdown)
        self.assertIn("你主导", markdown)
        self.assertIn("Agent 接力", markdown)
        self.assertIn("深度创作", markdown)
        self.assertIn("CAS", markdown)

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
