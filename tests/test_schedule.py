import os
import tempfile
import unittest
from unittest import mock

from quota_butler import schedule


class TestScheduleEntrypoint(unittest.TestCase):
    def _config(self, body):
        d = tempfile.TemporaryDirectory()
        path = os.path.join(d.name, "config.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        return d, path

    @mock.patch("quota_butler.schedule.push_schedule_card")
    def test_run_pushes_schedule_card(self, push):
        tmp, path = self._config(
            "scheduler_mode: balanced\n"
            "scheduler_agents: cc,codex\n"
            "work_start: '08:00'\n"
            "sleep_time: '23:00'\n"
            "work_duration_hours: 8\n"
        )
        try:
            rc = schedule.run(path, intent="帮我安排明天", dry_run=True)
        finally:
            tmp.cleanup()

        self.assertEqual(rc, 0)
        plan = push.call_args.args[0]
        self.assertEqual(plan.agents, ("cc", "codex"))
        self.assertEqual(plan.work_start.strftime("%H:%M"), "08:00")
        push.assert_called_once()

    @mock.patch("quota_butler.schedule.push_schedule_card")
    def test_run_returns_two_for_bad_agent(self, push):
        tmp, path = self._config("scheduler_agents: cc,nope\n")
        try:
            rc = schedule.run(path, dry_run=True)
        finally:
            tmp.cleanup()

        self.assertEqual(rc, 2)
        push.assert_not_called()


if __name__ == "__main__":
    unittest.main()
