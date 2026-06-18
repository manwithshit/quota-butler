import os
import plistlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from quota_butler.config import Config
from quota_butler.plan_tasks import (
    PlanTaskError,
    _parse_datetime,
    cancel_plan_tasks,
    install_plan_tasks,
    plan_record,
)
from quota_butler.planner import build_plan


class TestPlanRecord(unittest.TestCase):
    def test_plan_record_contains_stable_id_and_events(self):
        start = datetime(2026, 6, 19, 9, 0, tzinfo=timezone.utc)
        plan = build_plan(
            mode="balanced",
            agents=("cc", "codex"),
            work_start=start,
            work_end=start + timedelta(hours=8),
        )
        first = plan_record(plan)
        second = plan_record(plan)

        self.assertEqual(first["plan_id"], second["plan_id"])
        self.assertEqual(first["status"], "proposed")
        self.assertTrue(any(event["kind"] == "warmup" for event in first["events"]))

    @mock.patch(
        "quota_butler.plan_tasks._local_timezone",
        return_value=timezone(timedelta(hours=8)),
    )
    def test_naive_plan_time_is_interpreted_as_local_time(self, local_timezone):
        parsed = _parse_datetime("2026-06-19T06:30:00")
        self.assertEqual(parsed.hour, 6)
        self.assertEqual(parsed.utcoffset(), timedelta(hours=8))


class TestPlanTasks(unittest.TestCase):
    @mock.patch("quota_butler.plan_tasks.subprocess.run")
    def test_install_writes_future_warmups_and_bootstraps_them(self, run):
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(plan_tasks_dir=d)
            now = datetime(2026, 6, 18, 8, 0, tzinfo=timezone.utc)
            record = {
                "plan_id": "abc123",
                "status": "proposed",
                "work_start": "2026-06-18T08:00:00+00:00",
                "work_end": "2026-06-18T13:00:00+00:00",
                "events": [
                    {"agent": "cc", "kind": "warmup", "at": "2026-06-18T07:00:00+00:00"},
                    {"agent": "codex", "kind": "warmup", "at": "2026-06-18T09:30:00+00:00"},
                    {"agent": "cc", "kind": "recovery", "at": "2026-06-18T12:00:00+00:00"},
                    {"agent": "codex", "kind": "recovery", "at": "2026-06-18T14:30:00+00:00"},
                ],
            }

            config_path = os.path.join(d, "config.yaml")
            tasks = install_plan_tasks(record, cfg, now=now, config_path=config_path)

            self.assertEqual(len(tasks), 2)
            self.assertTrue(os.path.exists(tasks[0]["plist_path"]))
            with open(tasks[0]["plist_path"], "rb") as f:
                plist = plistlib.load(f)
            self.assertIn("scheduled_warmup", " ".join(plist["ProgramArguments"]))
            self.assertIn(config_path, plist["ProgramArguments"])
            self.assertEqual(run.call_count, 2)

    @mock.patch("quota_butler.plan_tasks.subprocess.run")
    def test_install_rolls_back_when_launchctl_fails(self, run):
        run.return_value = mock.Mock(returncode=5, stdout="", stderr="bootstrap failed")
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(plan_tasks_dir=d)
            record = {
                "plan_id": "abc123",
                "work_start": "2099-06-18T08:00:00+00:00",
                "work_end": "2099-06-18T13:00:00+00:00",
                "events": [
                    {"agent": "codex", "kind": "warmup", "at": "2099-06-18T09:30:00+00:00"},
                ],
            }
            with self.assertRaises(PlanTaskError):
                install_plan_tasks(record, cfg)
            self.assertEqual(os.listdir(d), [])

    @mock.patch("quota_butler.plan_tasks.subprocess.run")
    def test_install_wraps_launchctl_os_error(self, run):
        run.side_effect = OSError("launchctl missing")
        with tempfile.TemporaryDirectory() as d:
            cfg = Config(plan_tasks_dir=d)
            record = {
                "plan_id": "abc123",
                "work_start": "2099-06-18T08:00:00+00:00",
                "work_end": "2099-06-18T13:00:00+00:00",
                "events": [
                    {"agent": "codex", "kind": "warmup", "at": "2099-06-18T09:30:00+00:00"},
                ],
            }
            with self.assertRaises(PlanTaskError):
                install_plan_tasks(record, cfg)

    @mock.patch("quota_butler.plan_tasks.subprocess.run")
    def test_cancel_boots_out_and_removes_task_files(self, run):
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "task.plist")
            with open(path, "wb") as f:
                plistlib.dump({"Label": "com.quota-butler.plan.test"}, f)

            cancel_plan_tasks([{"label": "com.quota-butler.plan.test", "plist_path": path}])

            self.assertFalse(os.path.exists(path))
            run.assert_called_once()


if __name__ == "__main__":
    unittest.main()
