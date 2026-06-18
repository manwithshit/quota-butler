import unittest
from datetime import date

from quota_butler.schedule_flow import (
    FLOW_VERSION,
    SchedulePreferences,
    flow_payload,
    parse_preferences,
    validate_flow_context,
    validate_work_time,
)


class TestSchedulePreferences(unittest.TestCase):
    def test_defaults_are_normal_mixed_work_from_nine_to_five(self):
        prefs = SchedulePreferences()

        self.assertEqual(prefs.task_type, "mixed")
        self.assertEqual(prefs.intensity, "normal")
        self.assertEqual(prefs.work_start, "09:00")
        self.assertEqual(prefs.work_end, "17:00")

    def test_parse_preferences_normalizes_chinese_labels(self):
        prefs = parse_preferences({
            "task_type": "编码开发",
            "intensity": "高强度",
            "work_start": "10:05",
            "work_end": "18:30",
        })

        self.assertEqual(prefs.task_type, "coding")
        self.assertEqual(prefs.intensity, "high")
        self.assertEqual(prefs.work_start, "10:05")
        self.assertEqual(prefs.work_end, "18:30")

    def test_parse_preferences_rejects_unknown_choices(self):
        with self.assertRaisesRegex(ValueError, "任务类型"):
            parse_preferences({"task_type": "打游戏"})
        with self.assertRaisesRegex(ValueError, "工作强度"):
            parse_preferences({"intensity": "爆肝"})

    def test_validate_work_time_requires_later_end(self):
        with self.assertRaisesRegex(ValueError, "晚于"):
            validate_work_time("17:00", "09:00")
        with self.assertRaisesRegex(ValueError, "晚于"):
            validate_work_time("09:00", "09:00")

    def test_validate_work_time_caps_duration_at_sixteen_hours(self):
        self.assertEqual(validate_work_time("06:00", "22:00"), 16 * 60)
        with self.assertRaisesRegex(ValueError, "16"):
            validate_work_time("05:59", "22:00")

    def test_flow_payload_preserves_complete_context(self):
        prefs = SchedulePreferences(
            task_type="research",
            intensity="light",
            work_start="10:00",
            work_end="16:00",
        )

        payload = flow_payload(
            step="summary",
            target_date=date(2026, 6, 19),
            preferences=prefs,
        )

        self.assertEqual(payload["cmd"], "quota")
        self.assertEqual(payload["action"], "schedule_flow")
        self.assertEqual(payload["flow_version"], FLOW_VERSION)
        self.assertEqual(payload["step"], "summary")
        self.assertEqual(payload["target_date"], "2026-06-19")
        self.assertEqual(payload["preferences"], {
            "task_type": "research",
            "intensity": "light",
            "work_start": "10:00",
            "work_end": "16:00",
        })

    def test_validate_flow_context_rejects_old_version_and_past_date(self):
        valid = {
            "flow_version": FLOW_VERSION,
            "target_date": "2026-06-19",
        }
        self.assertEqual(
            validate_flow_context(valid, today=date(2026, 6, 18)),
            date(2026, 6, 19),
        )
        with self.assertRaisesRegex(ValueError, "版本"):
            validate_flow_context(
                {**valid, "flow_version": 1},
                today=date(2026, 6, 18),
            )
        with self.assertRaisesRegex(ValueError, "过期"):
            validate_flow_context(
                {**valid, "target_date": "2026-06-17"},
                today=date(2026, 6, 18),
            )


if __name__ == "__main__":
    unittest.main()
