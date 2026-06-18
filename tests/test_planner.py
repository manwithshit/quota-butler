import unittest
from datetime import date, datetime, timedelta

from quota_butler.config import Config
from quota_butler.planner import (
    build_plan,
    normalize_mode,
    parse_agents,
    plan_from_config,
    plan_from_preferences,
)
from quota_butler.schedule_flow import SchedulePreferences


class TestPlanner(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 6, 18, 8, 0)
        self.end = self.start + timedelta(hours=10)

    def test_single_agent_sustain_prewarms_one_window_before_work(self):
        plan = build_plan(mode="sustain", agents=("cc",), work_start=self.start, work_end=self.end)
        self.assertEqual(plan.agents, ("cc",))
        self.assertEqual(plan.events[0].kind, "warmup")
        self.assertEqual(plan.events[0].at, datetime(2026, 6, 18, 3, 0))
        self.assertEqual(plan.cas, 1.0)
        self.assertEqual(plan.waiting_minutes, 0.0)

    def test_dual_agent_sustain_staggers_half_window(self):
        plan = build_plan(
            mode="sustain",
            agents=("cc", "codex"),
            work_start=self.start,
            work_end=self.end,
        )
        warmups = [e for e in plan.events if e.kind == "warmup"]
        self.assertEqual([(e.agent, e.at.strftime("%H:%M")) for e in warmups], [
            ("cc", "03:00"),
            ("codex", "05:30"),
        ])
        recoveries = [e for e in plan.events if e.kind == "recovery"]
        self.assertIn(("cc", "08:00"), [(e.agent, e.at.strftime("%H:%M")) for e in recoveries])
        self.assertIn(("codex", "10:30"), [(e.agent, e.at.strftime("%H:%M")) for e in recoveries])
        self.assertEqual(plan.cas, 1.0)

    def test_chinese_mode_aliases(self):
        self.assertEqual(normalize_mode("不断粮模式"), "sustain")
        self.assertEqual(normalize_mode("今天冲刺"), "sustain")
        self.assertEqual(normalize_mode("节省模式"), "savings")

    def test_parse_agents_normalizes_and_dedups(self):
        self.assertEqual(parse_agents("Claude Code, codex, cc"), ("cc", "codex"))

    def test_invalid_agent_raises(self):
        with self.assertRaises(ValueError):
            parse_agents("cc,unknown")

    def test_plan_from_config_caps_at_sleep_time(self):
        cfg = Config(
            scheduler_mode="不断粮模式",
            scheduler_agents="cc",
            work_start="20:00",
            sleep_time="23:00",
            work_duration_hours=10,
        )
        plan = plan_from_config(cfg, target_date=date(2026, 6, 18))
        self.assertEqual(plan.work_start.strftime("%H:%M"), "20:00")
        self.assertEqual(plan.work_end.strftime("%H:%M"), "23:00")
        self.assertEqual(plan.work_hours, 3.0)

    def test_plan_from_config_accepts_runtime_agent_override(self):
        cfg = Config(
            scheduler_agents="cc,codex",
            work_start="09:00",
            sleep_time="23:00",
            work_duration_hours=8,
        )
        plan = plan_from_config(
            cfg,
            target_date=date(2026, 6, 18),
            agents=("codex",),
        )
        self.assertEqual(plan.agents, ("codex",))

    def test_guided_intensity_controls_advisory_relay_count(self):
        counts = {}
        for intensity in ("light", "normal", "high"):
            plan = plan_from_preferences(
                SchedulePreferences(intensity=intensity),
                target_date=date(2026, 6, 18),
                agents=("codex",),
            )
            counts[intensity] = plan.relay_count

        self.assertEqual(counts, {
            "light": 0,
            "normal": 1,
            "high": 3,
        })

    def test_guided_plan_is_versioned_and_retains_preferences(self):
        prefs = SchedulePreferences(
            task_type="research",
            intensity="normal",
            work_start="10:00",
            work_end="18:00",
        )

        plan = plan_from_preferences(
            prefs,
            target_date=date(2026, 6, 18),
            agents=("cc", "codex"),
        )

        self.assertEqual(plan.plan_version, 2)
        self.assertEqual(plan.preferences, prefs)
        self.assertEqual(plan.work_start, datetime(2026, 6, 18, 10, 0))
        self.assertEqual(plan.work_end, datetime(2026, 6, 18, 18, 0))

    def test_guided_relay_notes_reflect_task_type(self):
        expected = {
            "coding": "代码实现与测试",
            "content": "创作与审稿",
            "research": "资料与结论",
            "mixed": "当前任务与下一阶段",
        }

        for task_type, phrase in expected.items():
            plan = plan_from_preferences(
                SchedulePreferences(task_type=task_type, intensity="normal"),
                target_date=date(2026, 6, 18),
                agents=("codex",),
            )
            self.assertEqual(plan.relay_count, 1)
            self.assertIn(phrase, plan.relay_points[0].note)


if __name__ == "__main__":
    unittest.main()
