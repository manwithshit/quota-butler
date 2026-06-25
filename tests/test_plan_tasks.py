import os
import plistlib
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from unittest import mock

from quota_butler.config import Config
from quota_butler.plan_tasks import (
    PlanTaskError,
    cancel_plan_tasks,
    install_plan_tasks,
    plan_record,
    validate_plan_record,
)
from quota_butler.planner import build_plan
from quota_butler.providers.base import Usage, WindowUsage
from quota_butler.schedule_flow import PlanRequest


def _plan(strategy="both"):
    request = PlanRequest(
        date(2099, 6, 20),
        "range",
        "09:00",
        "18:00",
        strategy,
    )
    usages = {
        "cc": Usage("cc", WindowUsage(20, None, 5 * 3600)),
        "codex": Usage("codex", WindowUsage(30, None, 5 * 3600)),
    }
    return build_plan(request, usages)


class TestPlanRecord(unittest.TestCase):
    def test_v3_record_contains_only_user_relevant_plan_fields(self):
        record = plan_record(_plan())

        self.assertEqual(record["plan_version"], 3)
        self.assertEqual(record["request"]["agent_strategy"], "both")
        self.assertIn("purpose", record["events"][0])
        self.assertNotIn("cas", record)
        self.assertNotIn("preferences", record)

    def test_v2_record_is_rejected(self):
        with self.assertRaisesRegex(PlanTaskError, "失效"):
            validate_plan_record(
                {
                    "plan_id": "old",
                    "plan_version": 2,
                    "work_start": "2099-06-20T09:00:00",
                    "work_end": "2099-06-20T14:00:00",
                    "events": [],
                }
            )


class TestPlanTasks(unittest.TestCase):
    @mock.patch("quota_butler.plan_tasks.subprocess.run")
    def test_install_creates_independent_tasks_for_both_agents(self, run):
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as directory:
            config = Config(plan_tasks_dir=directory)
            record = plan_record(_plan())

            config_path = os.path.join(directory, "config.yaml")
            lark_config = os.path.join(directory, "lark-cli")
            with mock.patch.dict(
                os.environ,
                {
                    "LARK_CHANNEL": "codex",
                    "LARKSUITE_CLI_CONFIG_DIR": lark_config,
                },
            ):
                tasks = install_plan_tasks(
                    record,
                    config,
                    now=datetime(2099, 6, 19, tzinfo=timezone.utc),
                    config_path=config_path,
                )

            providers = [task["provider"] for task in tasks]
            self.assertIn("cc", providers)
            self.assertIn("codex", providers)
            self.assertEqual(run.call_count, len(tasks))
            for task in tasks:
                with open(task["plist_path"], "rb") as stream:
                    plist = plistlib.load(stream)
                argv = " ".join(plist["ProgramArguments"])
                self.assertIn("scheduled_warmup", argv)
                self.assertIn(task["provider"], argv)
                env = plist["EnvironmentVariables"]
                self.assertEqual(env["LARK_CHANNEL"], "codex")
                self.assertEqual(env["LARKSUITE_CLI_CONFIG_DIR"], lark_config)
                self.assertEqual(env["QUOTA_BUTLER_CONFIG"], config_path)
                self.assertEqual(env["QUOTA_BUTLER_PYTHON"], sys.executable)
                self.assertIn(os.path.dirname(sys.executable), env["PATH"])

    @mock.patch("quota_butler.plan_tasks.subprocess.run")
    def test_install_rolls_back_all_tasks_when_one_bootstrap_fails(self, run):
        run.side_effect = [
            mock.Mock(returncode=0, stdout="", stderr=""),
            mock.Mock(returncode=5, stdout="", stderr="failed"),
            mock.Mock(returncode=0, stdout="", stderr=""),
        ]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PlanTaskError):
                install_plan_tasks(
                    plan_record(_plan()),
                    Config(plan_tasks_dir=directory),
                    now=datetime(2099, 6, 19, tzinfo=timezone.utc),
                )
            self.assertFalse([name for name in os.listdir(directory) if name.endswith(".plist")])

    @mock.patch("quota_butler.plan_tasks.subprocess.run")
    def test_cancel_removes_all_task_files(self, run):
        run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(2):
                path = os.path.join(directory, f"task-{index}.plist")
                with open(path, "wb") as stream:
                    plistlib.dump({"Label": f"task-{index}"}, stream)
                paths.append({"label": f"task-{index}", "plist_path": path})

            cancel_plan_tasks(paths)

            self.assertTrue(all(not os.path.exists(item["plist_path"]) for item in paths))


if __name__ == "__main__":
    unittest.main()
