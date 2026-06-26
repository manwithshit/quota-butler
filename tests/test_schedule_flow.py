import unittest
from datetime import date

from quota_butler.schedule_flow import (
    FLOW_VERSION,
    PlanRequest,
    flow_payload,
    parse_plan_request,
    validate_flow_context,
    validate_warmup_times,
)


class TestPlanRequest(unittest.TestCase):
    def test_v3_flow_invalidates_old_guided_cards(self):
        self.assertEqual(FLOW_VERSION, 5)

    def test_single_start_defaults_to_two_warmups_with_one_agent(self):
        request = parse_plan_request(
            {
                "target_date": "2026-06-20",
                "time_mode": "point",
                "work_start": "09:00",
            },
            available_agent_count=1,
        )

        self.assertEqual(request.work_start, "09:00")
        self.assertEqual(request.first_warmup, "06:30")
        self.assertEqual(request.second_warmup, "11:31")
        self.assertEqual(request.work_end, "16:31")
        self.assertEqual(request.agent_strategy, "auto")

    def test_single_start_uses_same_single_agent_defaults_with_two_agents(self):
        request = parse_plan_request(
            {
                "target_date": "2026-06-20",
                "time_mode": "point",
                "work_start": "09:00 +0800",
            },
            available_agent_count=2,
        )

        self.assertEqual(request.first_warmup, "06:30")
        self.assertEqual(request.second_warmup, "11:31")
        self.assertEqual(request.work_end, "16:31")

    def test_explicit_range_is_limited_to_sixteen_hours(self):
        request = parse_plan_request(
            {
                "target_date": "2026-06-20",
                "time_mode": "range",
                "work_start": "09:00",
                "work_end": "18:00",
                "agent_strategy": "codex",
            },
            available_agent_count=2,
        )
        self.assertEqual(request.agent_strategy, "codex")
        self.assertEqual(request.work_end, "18:00")

        with self.assertRaisesRegex(ValueError, "16"):
            parse_plan_request(
                {
                    "target_date": "2026-06-20",
                    "time_mode": "range",
                    "work_start": "05:00",
                    "work_end": "22:00",
                },
                available_agent_count=2,
            )

    def test_flow_payload_preserves_only_adjustable_variables(self):
        request = PlanRequest(
            target_date=date(2026, 6, 20),
            time_mode="range",
            work_start="12:00",
            work_end="18:00",
            agent_strategy="cc",
        )

        payload = flow_payload("generate_plan", request)

        self.assertEqual(payload["action"], "schedule_flow")
        self.assertEqual(payload["flow_version"], FLOW_VERSION)
        self.assertEqual(payload["request"], {
            "target_date": "2026-06-20",
            "time_mode": "range",
            "work_start": "12:00",
            "work_end": "18:00",
            "agent_strategy": "cc",
            "first_warmup": "",
            "second_warmup": "",
        })
        self.assertNotIn("task_type", str(payload))
        self.assertNotIn("intensity", str(payload))

    def test_warmup_times_need_five_hour_gap(self):
        self.assertEqual(validate_warmup_times("06:30", "11:30"), 300)
        with self.assertRaisesRegex(ValueError, "5 小时"):
            validate_warmup_times("06:30", "07:30")

    def test_validate_context_rejects_old_version(self):
        with self.assertRaisesRegex(ValueError, "失效"):
            validate_flow_context(
                {"flow_version": 3, "target_date": "2026-06-20"},
                today=date(2026, 6, 19),
            )


if __name__ == "__main__":
    unittest.main()
