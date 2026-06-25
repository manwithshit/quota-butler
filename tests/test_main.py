import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from quota_butler import main
from quota_butler import state as state_mod
from quota_butler.agent_status import AgentState, AgentStatus
from quota_butler.notify import NotifyError
from quota_butler.providers.base import Usage, WindowUsage

LOCAL = timezone(timedelta(hours=8))


def _config_file():
    directory = tempfile.mkdtemp()
    config_path = os.path.join(directory, "config.yaml")
    state_path = os.path.join(directory, "state.json")
    with open(config_path, "w", encoding="utf-8") as stream:
        stream.write(
            f"state_path: {state_path}\n"
            "feishu:\n"
            "  chat_id: oc_test\n"
        )
    return config_path, state_path


def _connected(provider, utilization=0, reset=None):
    return AgentStatus(
        provider,
        AgentState.CONNECTED,
        executable=f"/usr/local/bin/{provider}",
        usage=Usage(provider, WindowUsage(utilization, reset, 5 * 3600)),
    )


class TestMainV3(unittest.TestCase):
    def setUp(self):
        self.config_path, self.state_path = _config_file()

    @mock.patch("quota_butler.main.push_interactive")
    @mock.patch("quota_butler.main.detect_agents")
    def test_recovered_window_sends_one_actionable_card(self, detect, push):
        detect.return_value = {"cc": _connected("cc", 0)}
        state_mod.save(
            self.state_path,
            state_mod.State(
                provider_snapshots={
                    "cc": {
                        "utilization": 80,
                        "reset_at": "2026-06-19T13:30:00+08:00",
                    }
                }
            ),
        )

        rc = main.run(
            self.config_path,
            now=datetime(2026, 6, 19, 14, 0, tzinfo=LOCAL),
        )

        self.assertEqual(rc, 0)
        self.assertEqual(push.call_count, 1)
        self.assertIn("立即预热", str(push.call_args.args[0]))

    @mock.patch("quota_butler.main.push_interactive")
    @mock.patch("quota_butler.main.detect_agents")
    def test_recovered_window_uses_bound_p2p_notification_target(self, detect, push):
        detect.return_value = {"codex": _connected("codex", 0)}
        state_mod.save(
            self.state_path,
            state_mod.State(
                provider_snapshots={
                    "codex": {
                        "utilization": 80,
                        "reset_at": "2026-06-19T13:30:00+08:00",
                    }
                },
                notification_target={"chat_id": "oc_p2p", "chat_type": "p2p"},
            ),
        )
        with open(self.config_path, "w", encoding="utf-8") as stream:
            stream.write(f"state_path: {self.state_path}\nfeishu:\n  chat_id: ''\n")

        rc = main.run(
            self.config_path,
            now=datetime(2026, 6, 19, 14, 0, tzinfo=LOCAL),
        )

        self.assertEqual(rc, 0)
        cfg = push.call_args.args[1]
        self.assertEqual(cfg.feishu.chat_id, "oc_p2p")
        self.assertEqual(
            state_mod.load(self.state_path).last_recovery_notified_windows["codex"],
            "codex:2026-06-19T13:30:00+08:00",
        )

    @mock.patch("quota_butler.main.push_interactive")
    @mock.patch("quota_butler.main.detect_agents")
    def test_recovered_window_without_target_is_retryable(self, detect, push):
        detect.return_value = {"codex": _connected("codex", 0)}
        push.side_effect = NotifyError("config.feishu 未配置 message_id / chat_id / user_id")
        state_mod.save(
            self.state_path,
            state_mod.State(
                provider_snapshots={
                    "codex": {
                        "utilization": 80,
                        "reset_at": "2026-06-19T13:30:00+08:00",
                    }
                },
            ),
        )
        with open(self.config_path, "w", encoding="utf-8") as stream:
            stream.write(f"state_path: {self.state_path}\nfeishu:\n  chat_id: ''\n")

        rc = main.run(
            self.config_path,
            now=datetime(2026, 6, 19, 14, 0, tzinfo=LOCAL),
        )

        self.assertEqual(rc, 3)
        self.assertIsNone(state_mod.load(self.state_path).last_recovery_notified_windows)

    @mock.patch("quota_butler.main.push_interactive")
    @mock.patch("quota_butler.main.detect_agents")
    def test_recovery_during_23_to_08_quiet_hours_is_not_sent(self, detect, push):
        detect.return_value = {"codex": _connected("codex", 0)}
        state_mod.save(
            self.state_path,
            state_mod.State(
                provider_snapshots={
                    "codex": {
                        "utilization": 90,
                        "reset_at": "2026-06-19T01:00:00+08:00",
                    }
                }
            ),
        )

        rc = main.run(
            self.config_path,
            now=datetime(2026, 6, 19, 1, 30, tzinfo=LOCAL),
        )

        self.assertEqual(rc, 0)
        push.assert_not_called()

    @mock.patch("quota_butler.main.push_interactive")
    @mock.patch("quota_butler.main.detect_agents")
    def test_22_oclock_sends_bedtime_card_only_once_per_day(self, detect, push):
        detect.return_value = {"cc": _connected("cc", 30)}
        now = datetime(2026, 6, 19, 22, 5, tzinfo=LOCAL)

        self.assertEqual(main.run(self.config_path, now=now), 0)
        self.assertEqual(main.run(self.config_path, now=now), 0)

        self.assertEqual(push.call_count, 1)
        self.assertIn("明天有重度使用 AI", str(push.call_args.args[0]))

    @mock.patch("quota_butler.main.push_interactive")
    @mock.patch("quota_butler.main.detect_agents")
    def test_22_oclock_recovery_is_merged_into_bedtime_card(self, detect, push):
        detect.return_value = {"cc": _connected("cc", 0)}
        state_mod.save(
            self.state_path,
            state_mod.State(
                provider_snapshots={
                    "cc": {
                        "utilization": 90,
                        "reset_at": "2026-06-19T21:59:00+08:00",
                    }
                }
            ),
        )

        rc = main.run(
            self.config_path,
            now=datetime(2026, 6, 19, 22, 0, tzinfo=LOCAL),
        )

        self.assertEqual(rc, 0)
        self.assertEqual(push.call_count, 1)
        card = str(push.call_args.args[0])
        self.assertIn("刚刚恢复", card)
        self.assertIn("明天有重度使用 AI", card)

    @mock.patch("quota_butler.main.push_interactive")
    @mock.patch("quota_butler.main.detect_agents")
    def test_old_night_recovery_is_not_replayed_after_eight(self, detect, push):
        detect.return_value = {"cc": _connected("cc", 0)}
        state_mod.save(
            self.state_path,
            state_mod.State(
                provider_snapshots={
                    "cc": {
                        "utilization": 90,
                        "reset_at": "2026-06-19T01:00:00+08:00",
                    }
                }
            ),
        )

        rc = main.run(
            self.config_path,
            now=datetime(2026, 6, 19, 9, 0, tzinfo=LOCAL),
        )

        self.assertEqual(rc, 0)
        push.assert_not_called()

    @mock.patch("quota_butler.main.push_interactive")
    @mock.patch("quota_butler.main.detect_agents")
    def test_recent_night_recovery_is_not_replayed_after_eight(self, detect, push):
        detect.return_value = {"codex": _connected("codex", 0)}
        state_mod.save(
            self.state_path,
            state_mod.State(
                provider_snapshots={
                    "codex": {
                        "utilization": 90,
                        "reset_at": "2026-06-19T07:30:00+08:00",
                    }
                }
            ),
        )

        rc = main.run(
            self.config_path,
            now=datetime(2026, 6, 19, 8, 15, tzinfo=LOCAL),
        )

        self.assertEqual(rc, 0)
        push.assert_not_called()

    @mock.patch("quota_butler.main.push_interactive")
    @mock.patch("quota_butler.main.detect_agents")
    def test_due_snooze_repeats_the_same_recovery_card(self, detect, push):
        detect.return_value = {"codex": _connected("codex", 0)}
        state_mod.save(
            self.state_path,
            state_mod.State(
                pending_recovery={
                    "provider": "codex",
                    "window_key": "codex:w1",
                    "due_at": "2026-06-19T13:59:00+08:00",
                },
                last_recovery_notified_windows={"codex": "codex:w1"},
            ),
        )

        rc = main.run(
            self.config_path,
            now=datetime(2026, 6, 19, 14, 0, tzinfo=LOCAL),
        )

        self.assertEqual(rc, 0)
        self.assertEqual(push.call_count, 1)
        self.assertIn("30 分钟后提醒", str(push.call_args.args[0]))
        self.assertIsNone(state_mod.load(self.state_path).pending_recovery)


if __name__ == "__main__":
    unittest.main()
