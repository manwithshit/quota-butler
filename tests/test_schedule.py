import unittest
from unittest import mock

from quota_butler import schedule


class TestScheduleEntrypoint(unittest.TestCase):
    @mock.patch("quota_butler.schedule.handler.handle")
    def test_cli_opens_v3_time_flow(self, handle):
        handle.return_value = 0

        rc = schedule.run("/tmp/config.yaml", intent="tomorrow", dry_run=True)

        self.assertEqual(rc, 0)
        handle.assert_called_once_with(
            {"action": "schedule_intent", "intent": "tomorrow"},
            config_path="/tmp/config.yaml",
            dry_run=True,
        )


if __name__ == "__main__":
    unittest.main()
