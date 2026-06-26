import unittest
from datetime import date, datetime, timedelta, timezone

from quota_butler.planner import build_plan, parse_agents
from quota_butler.providers.base import Usage, WindowUsage
from quota_butler.schedule_flow import PlanRequest


class TestPlanner(unittest.TestCase):
    def test_single_agent_plan_shows_two_exact_warmup_nodes(self):
        request = PlanRequest(
            target_date=date(2026, 6, 20),
            time_mode="point",
            work_start="09:00",
            work_end="14:00",
            agent_strategy="auto",
        )

        plan = build_plan(request, {"cc": self._usage("cc", 55)})

        self.assertEqual(plan.plan_version, 3)
        self.assertEqual(plan.agents, ("cc",))
        self.assertEqual(
            [(event.agent, event.at.strftime("%H:%M"), event.purpose) for event in plan.events],
            [
                ("cc", "06:30", "准备第一个窗口"),
                ("cc", "11:31", "恢复后准备第二个窗口"),
            ],
        )
        self.assertEqual(plan.work_start.strftime("%H:%M"), "09:00")
        self.assertEqual(plan.work_end.strftime("%H:%M"), "14:00")

    def test_adjusting_start_time_recalculates_all_warmups(self):
        request = PlanRequest(
            target_date=date(2026, 6, 20),
            time_mode="point",
            work_start="12:00",
            work_end="17:00",
            agent_strategy="cc",
        )

        plan = build_plan(request, {"cc": self._usage("cc", 20)})

        self.assertEqual(
            [event.at.strftime("%H:%M") for event in plan.events],
            ["09:30", "14:31"],
        )

    def test_auto_uses_one_agent_for_short_range_even_when_two_are_available(self):
        request = PlanRequest(
            target_date=date(2026, 6, 20),
            time_mode="range",
            work_start="09:00",
            work_end="13:00",
            agent_strategy="auto",
        )

        plan = build_plan(
            request,
            {
                "cc": self._usage("cc", 70),
                "codex": self._usage("codex", 20),
            },
        )

        self.assertEqual(plan.agents, ("codex",))
        self.assertIn("只使用 Codex", plan.reason)

    def test_auto_ranks_weekly_quota_before_five_hour_quota(self):
        request = PlanRequest(
            target_date=date(2026, 6, 20),
            time_mode="range",
            work_start="09:00",
            work_end="18:00",
            agent_strategy="auto",
        )

        plan = build_plan(
            request,
            {
                "cc": self._usage("cc", 10, weekly_utilization=95),
                "codex": self._usage("codex", 30, weekly_utilization=20),
            },
        )

        self.assertEqual(plan.agents, ("codex",))

    def test_explicit_agent_control_overrides_auto_selection(self):
        request = PlanRequest(
            target_date=date(2026, 6, 20),
            time_mode="range",
            work_start="09:00",
            work_end="13:00",
            agent_strategy="cc",
        )

        plan = build_plan(
            request,
            {
                "cc": self._usage("cc", 70),
                "codex": self._usage("codex", 20),
            },
        )

        self.assertEqual(plan.agents, ("cc",))

    def test_both_strategy_is_rejected_in_single_agent_flow(self):
        request = PlanRequest(
            target_date=date(2026, 6, 20),
            time_mode="range",
            work_start="09:00",
            work_end="18:00",
            agent_strategy="both",
        )

        with self.assertRaisesRegex(ValueError, "一次只编排一个"):
            build_plan(
                request,
                {
                    "cc": self._usage("cc", 20),
                    "codex": self._usage("codex", 30),
                },
            )

    def test_selected_unavailable_agent_is_rejected(self):
        request = PlanRequest(
            target_date=date(2026, 6, 20),
            time_mode="point",
            work_start="09:00",
            work_end="14:00",
            agent_strategy="cc",
        )

        with self.assertRaisesRegex(ValueError, "Claude Code"):
            build_plan(request, {"codex": self._usage("codex", 20)})

    def test_parse_agents_still_normalizes_provider_names(self):
        self.assertEqual(parse_agents("Claude Code,codex,cc"), ("cc", "codex"))

    @staticmethod
    def _usage(provider, utilization, weekly_utilization=None):
        weekly = None
        if weekly_utilization is not None:
            weekly = WindowUsage(
                weekly_utilization,
                datetime(2026, 6, 25, 9, 0, tzinfo=timezone.utc),
                7 * 24 * 3600,
                "weekly",
            )
        return Usage(
            provider,
            WindowUsage(
                utilization,
                datetime(2026, 6, 20, 11, 30, tzinfo=timezone.utc),
                5 * 3600,
                "five_hour",
            ),
            weekly,
        )


if __name__ == "__main__":
    unittest.main()
