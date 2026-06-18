import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from unittest import mock

from quota_butler.main import run
from quota_butler.providers.base import Usage, WindowUsage
from quota_butler import state as state_mod


class TestMainRun(unittest.TestCase):
    def test_invalid_sense_provider_returns_read_failure(self):
        with tempfile.TemporaryDirectory() as d:
            config_path = os.path.join(d, "config.yaml")
            state_path = os.path.join(d, "state.json")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(
                    "sense_provider: bogus\n"
                    f"state_path: {state_path}\n"
                )

            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = run(config_path)

        self.assertEqual(code, 2)
        self.assertIn("未知 provider", stderr.getvalue())

    @mock.patch("quota_butler.main.push_oneup_card")
    @mock.patch("quota_butler.main.get_provider")
    def test_secondary_agent_can_push_oneup_when_primary_read_fails(
        self, get_provider, push_oneup
    ):
        from quota_butler.providers.base import ProviderError

        now = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
        cc = mock.Mock()
        cc.read_usage.side_effect = ProviderError("Claude token expired")
        codex = mock.Mock()
        codex.read_usage.return_value = Usage(
            provider="codex",
            five_hour=WindowUsage(0, None, 5 * 3600),
        )
        get_provider.side_effect = lambda name: {"cc": cc, "codex": codex}[name]
        with tempfile.TemporaryDirectory() as d:
            config_path = os.path.join(d, "config.yaml")
            state_path = os.path.join(d, "state.json")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(
                    "sense_provider: cc\n"
                    "scheduler_agents: cc,codex\n"
                    f"state_path: {state_path}\n"
                    "feishu:\n"
                    "  chat_id: oc_test\n"
                )
            previous_reset = (now - timedelta(minutes=1)).isoformat()
            state_mod.save(
                state_path,
                state_mod.State(
                    provider_snapshots={
                        "codex": {"utilization": 70, "reset_at": previous_reset}
                    }
                ),
            )

            code = run(config_path, now=now)

            self.assertEqual(code, 0)
            push_oneup.assert_called_once()

    @mock.patch("quota_butler.main.cancel_plan_tasks")
    @mock.patch("quota_butler.main.get_provider")
    def test_expired_plan_cleanup_persists_even_when_sensing_fails(
        self, get_provider, cancel
    ):
        from quota_butler.providers.base import ProviderError

        now = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
        get_provider.return_value.read_usage.side_effect = ProviderError("offline")
        with tempfile.TemporaryDirectory() as d:
            config_path = os.path.join(d, "config.yaml")
            state_path = os.path.join(d, "state.json")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(
                    "sense_provider: codex\n"
                    "scheduler_agents: codex\n"
                    f"state_path: {state_path}\n"
                )
            state_mod.save(
                state_path,
                state_mod.State(
                    active_plan={
                        "plan_id": "old",
                        "status": "active",
                        "work_end": (now - timedelta(minutes=1)).isoformat(),
                        "tasks": [],
                    }
                ),
            )

            code = run(config_path, now=now)

            self.assertEqual(code, 2)
            self.assertIsNone(state_mod.load(state_path).active_plan)

    @mock.patch("quota_butler.main.push_oneup_card")
    @mock.patch("quota_butler.main.get_provider")
    def test_recovered_provider_without_active_plan_pushes_oneup(
        self, get_provider, push_oneup
    ):
        now = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
        usage = Usage(
            provider="codex",
            five_hour=WindowUsage(0, None, 5 * 3600),
        )
        get_provider.return_value.read_usage.return_value = usage
        with tempfile.TemporaryDirectory() as d:
            config_path = os.path.join(d, "config.yaml")
            state_path = os.path.join(d, "state.json")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(
                    "sense_provider: codex\n"
                    "scheduler_agents: codex\n"
                    f"state_path: {state_path}\n"
                    "feishu:\n"
                    "  chat_id: oc_test\n"
                )
            previous_reset = (now - timedelta(minutes=1)).isoformat()
            state_mod.save(
                state_path,
                state_mod.State(
                    provider_snapshots={
                        "codex": {"utilization": 80, "reset_at": previous_reset}
                    }
                ),
            )

            code = run(config_path, now=now)

            self.assertEqual(code, 0)
            push_oneup.assert_called_once()
            state = state_mod.load(state_path)
            self.assertEqual(
                state.last_oneup_notified_window,
                f"codex:{previous_reset}",
            )

    @mock.patch("quota_butler.main.push_oneup_card")
    @mock.patch("quota_butler.main.get_provider")
    def test_claude_recovery_does_not_offer_real_warmup(
        self, get_provider, push_oneup
    ):
        now = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
        get_provider.return_value.read_usage.return_value = Usage(
            provider="cc",
            five_hour=WindowUsage(0, None, 5 * 3600),
        )
        with tempfile.TemporaryDirectory() as d:
            config_path = os.path.join(d, "config.yaml")
            state_path = os.path.join(d, "state.json")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(
                    "sense_provider: cc\n"
                    "scheduler_agents: cc\n"
                    f"state_path: {state_path}\n"
                )
            previous_reset = (now - timedelta(minutes=1)).isoformat()
            state_mod.save(
                state_path,
                state_mod.State(
                    provider_snapshots={
                        "cc": {"utilization": 80, "reset_at": previous_reset}
                    }
                ),
            )

            code = run(config_path, now=now)

            self.assertEqual(code, 0)
            push_oneup.assert_not_called()

    @mock.patch("quota_butler.main.push_oneup_card")
    @mock.patch("quota_butler.main.get_provider")
    def test_active_plan_suppresses_main_oneup(self, get_provider, push_oneup):
        now = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
        get_provider.return_value.read_usage.return_value = Usage(
            provider="codex",
            five_hour=WindowUsage(0, None, 5 * 3600),
        )
        with tempfile.TemporaryDirectory() as d:
            config_path = os.path.join(d, "config.yaml")
            state_path = os.path.join(d, "state.json")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(
                    "sense_provider: codex\n"
                    "scheduler_agents: codex\n"
                    f"state_path: {state_path}\n"
                )
            state_mod.save(
                state_path,
                state_mod.State(
                    active_plan={"plan_id": "p1", "status": "active"},
                    provider_snapshots={
                        "codex": {
                            "utilization": 80,
                            "reset_at": (now - timedelta(minutes=1)).isoformat(),
                        }
                    },
                ),
            )

            code = run(config_path, now=now)

            self.assertEqual(code, 0)
            push_oneup.assert_not_called()

    @mock.patch("quota_butler.main.cancel_plan_tasks")
    @mock.patch("quota_butler.main.get_provider")
    def test_expired_active_plan_is_cleared_before_oneup_decision(
        self, get_provider, cancel
    ):
        now = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
        get_provider.return_value.read_usage.return_value = Usage(
            provider="codex",
            five_hour=WindowUsage(0, None, 5 * 3600),
        )
        with tempfile.TemporaryDirectory() as d:
            config_path = os.path.join(d, "config.yaml")
            state_path = os.path.join(d, "state.json")
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(
                    "sense_provider: codex\n"
                    "scheduler_agents: codex\n"
                    f"state_path: {state_path}\n"
                )
            state_mod.save(
                state_path,
                state_mod.State(
                    active_plan={
                        "plan_id": "old",
                        "status": "active",
                        "work_end": (now - timedelta(minutes=1)).isoformat(),
                        "tasks": [{"label": "old-task", "plist_path": "/tmp/old.plist"}],
                    }
                ),
            )

            code = run(config_path, now=now)

            self.assertEqual(code, 0)
            self.assertIsNone(state_mod.load(state_path).active_plan)
            cancel.assert_called_once()


if __name__ == "__main__":
    unittest.main()
