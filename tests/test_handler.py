import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

from quota_butler import handler
from quota_butler.agent_status import AgentState, AgentStatus
from quota_butler.providers.base import Usage, WindowUsage
from quota_butler.schedule_flow import FLOW_VERSION


def _config_file():
    directory = tempfile.mkdtemp()
    config_path = os.path.join(directory, "config.yaml")
    state_path = os.path.join(directory, "state.json")
    with open(config_path, "w", encoding="utf-8") as stream:
        stream.write(
            f"state_path: {state_path}\n"
            "warmup_prompt: say hi\n"
            "feishu:\n"
            "  chat_id: oc_test\n"
        )
    return config_path, state_path


def _connected(provider, window_seconds=5 * 3600, kind="five_hour"):
    return AgentStatus(
        provider,
        AgentState.CONNECTED,
        executable=f"/usr/local/bin/{provider}",
        usage=Usage(provider, WindowUsage(20, None, window_seconds, kind)),
    )


def _request(strategy="auto"):
    return {
        "target_date": (date.today() + timedelta(days=1)).isoformat(),
        "time_mode": "point",
        "work_start": "09:00",
        "work_end": "14:00",
        "agent_strategy": strategy,
    }


class TestHandler(unittest.TestCase):
    def setUp(self):
        self.config_path, self.state_path = _config_file()

    @mock.patch("quota_butler.handler.push_interactive")
    def test_schedule_intent_starts_with_time_mode_not_v2_questions(self, push):
        rc = handler.handle(
            {"action": "schedule_intent", "intent": "tomorrow"},
            config_path=self.config_path,
        )

        self.assertEqual(rc, 0)
        card = push.call_args.args[0]
        self.assertIn("重度使用时间", str(card))
        self.assertNotIn("task_type", str(card))
        self.assertNotIn("intensity", str(card))

    @mock.patch("quota_butler.handler.push_interactive")
    def test_time_mode_opens_native_point_picker(self, push):
        rc = handler.handle(
            {
                "action": "schedule_flow",
                "flow_version": FLOW_VERSION,
                "step": "edit_time_point",
                "target_date": _request()["target_date"],
                "request": _request(),
            },
            config_path=self.config_path,
        )

        self.assertEqual(rc, 0)
        self.assertIn("picker_time", str(push.call_args.args[0]))

    @mock.patch("quota_butler.handler.push_schedule_card")
    @mock.patch("quota_butler.handler.detect_agents")
    def test_generate_plan_reads_form_time_and_available_agents(self, detect, push):
        detect.return_value = {"cc": _connected("cc")}

        rc = handler.handle(
            {
                "action": "schedule_flow",
                "flow_version": FLOW_VERSION,
                "step": "generate_plan",
                "target_date": _request()["target_date"],
                "request": _request(),
                "form_value": {"work_start": "12:00"},
            },
            config_path=self.config_path,
        )

        self.assertEqual(rc, 0)
        plan = push.call_args.args[0]
        self.assertEqual(plan.work_start.strftime("%H:%M"), "12:00")
        self.assertEqual(
            [event.at.strftime("%H:%M") for event in plan.events],
            ["09:30", "14:30"],
        )

    @mock.patch("quota_butler.handler.push_receipt")
    @mock.patch("quota_butler.handler.detect_agents")
    def test_generate_plan_rejects_queryable_monthly_window(self, detect, receipt):
        detect.return_value = {
            "codex": _connected("codex", 30 * 86400, "monthly"),
        }

        rc = handler.handle(
            {
                "action": "schedule_flow",
                "flow_version": FLOW_VERSION,
                "step": "generate_plan",
                "target_date": _request()["target_date"],
                "request": _request(),
                "form_value": {"work_start": "12:00"},
            },
            config_path=self.config_path,
        )

        self.assertEqual(rc, 4)
        self.assertIn("暂时没有可用于规划", receipt.call_args.args[0])

    @mock.patch("quota_butler.handler.push_interactive")
    @mock.patch("quota_butler.handler.detect_agents")
    def test_adjust_agents_shows_selector_only_for_dual_agent(self, detect, push):
        detect.return_value = {"cc": _connected("cc"), "codex": _connected("codex")}

        rc = handler.handle(
            {"action": "adjust_schedule_agents", "request": _request()},
            config_path=self.config_path,
        )

        self.assertEqual(rc, 0)
        card = push.call_args.args[0]
        self.assertIn("两个都用", str(card))
        self.assertNotIn("自动安排", str(card))

    @mock.patch("quota_butler.handler.push_interactive")
    def test_adjust_time_reopens_picker_with_current_value(self, push):
        request = _request()
        request["work_start"] = "12:00"

        rc = handler.handle(
            {"action": "adjust_schedule_time", "request": request},
            config_path=self.config_path,
        )

        self.assertEqual(rc, 0)
        self.assertIn("12:00", str(push.call_args.args[0]))

    @mock.patch("quota_butler.handler.install_plan_tasks")
    @mock.patch("quota_butler.handler.detect_agents")
    @mock.patch("quota_butler.handler.push_receipt")
    def test_adopt_plan_authorizes_all_listed_agents(
        self, receipt, detect, install
    ):
        detect.return_value = {"cc": _connected("cc"), "codex": _connected("codex")}
        install.return_value = [
            {"provider": "cc", "label": "cc1", "status": "pending"},
            {"provider": "codex", "label": "cx1", "status": "pending"},
        ]
        plan = {
            "plan_id": "p123",
            "plan_version": 3,
            "status": "proposed",
            "agents": ["cc", "codex"],
            "work_start": "2099-06-20T09:00:00",
            "work_end": "2099-06-20T18:00:00",
            "events": [
                {
                    "agent": "cc",
                    "kind": "warmup",
                    "at": "2099-06-20T06:30:00",
                    "purpose": "准备第一个窗口",
                },
                {
                    "agent": "codex",
                    "kind": "warmup",
                    "at": "2099-06-20T13:50:00",
                    "purpose": "接力",
                },
            ],
        }

        rc = handler.handle(
            {"action": "adopt_schedule", "plan": plan},
            config_path=self.config_path,
        )

        self.assertEqual(rc, 0)
        install.assert_called_once()
        from quota_butler import state as state_mod
        self.assertEqual(state_mod.load(self.state_path).active_plan["status"], "active")
        self.assertIn("2 个预热任务", receipt.call_args.args[0])

    @mock.patch("quota_butler.handler.push_receipt")
    def test_old_v2_plan_is_rejected(self, receipt):
        rc = handler.handle(
            {
                "action": "adopt_schedule",
                "plan": {"plan_id": "old", "plan_version": 2},
            },
            config_path=self.config_path,
        )
        self.assertEqual(rc, 4)
        self.assertIn("失效", receipt.call_args.args[0])

    @mock.patch("quota_butler.handler.push_receipt")
    def test_older_preview_is_rejected_after_new_plan_is_generated(self, receipt):
        from quota_butler import state as state_mod
        state_mod.save(
            self.state_path,
            state_mod.State(proposed_plan_id="new-plan"),
        )

        rc = handler.handle(
            {
                "action": "adopt_schedule",
                "plan": {"plan_id": "old-plan", "plan_version": 3},
            },
            config_path=self.config_path,
        )

        self.assertEqual(rc, 4)
        self.assertIn("最新", receipt.call_args.args[0])

    @mock.patch("quota_butler.handler.push_receipt")
    @mock.patch("quota_butler.handler.get_provider")
    def test_immediate_warmup_supports_claude_without_second_confirmation(
        self, get_provider, receipt
    ):
        get_provider.return_value.warmup.return_value = "ok"

        rc = handler.handle(
            {"action": "warmup_now", "provider": "cc", "window_key": "cc:w1"},
            config_path=self.config_path,
        )

        self.assertEqual(rc, 0)
        get_provider.assert_called_once_with("cc")
        get_provider.return_value.warmup.assert_called_once_with("say hi")
        self.assertIn("已预热", receipt.call_args.args[0])

    @mock.patch("quota_butler.handler.push_receipt")
    @mock.patch("quota_butler.handler.get_provider")
    def test_scheduled_warmup_supports_claude_plan_task(self, get_provider, receipt):
        from quota_butler import state as state_mod
        state_mod.save(
            self.state_path,
            state_mod.State(
                active_plan={
                    "plan_id": "p123",
                    "plan_version": 3,
                    "status": "active",
                    "tasks": [
                        {
                            "provider": "cc",
                            "scheduled_for": "2099-06-20T06:30:00",
                            "status": "pending",
                        }
                    ],
                }
            ),
        )

        rc = handler.handle(
            {
                "action": "scheduled_warmup",
                "plan_id": "p123",
                "provider": "cc",
                "scheduled_for": "2099-06-20T06:30:00",
            },
            config_path=self.config_path,
        )

        self.assertEqual(rc, 0)
        get_provider.return_value.warmup.assert_called_once_with("say hi")
        saved = state_mod.load(self.state_path).active_plan
        self.assertEqual(saved["tasks"][0]["status"], "executed")

    @mock.patch("quota_butler.handler.push_receipt")
    def test_recovery_snooze_records_exact_window_for_later(self, receipt):
        rc = handler.handle(
            {
                "action": "recovery_snooze",
                "provider": "codex",
                "window_key": "codex:w1",
                "minutes": 30,
            },
            config_path=self.config_path,
        )

        self.assertEqual(rc, 0)
        from quota_butler import state as state_mod
        pending = state_mod.load(self.state_path).pending_recovery
        self.assertEqual(pending["provider"], "codex")
        self.assertEqual(pending["window_key"], "codex:w1")
        self.assertIn("due_at", pending)

    @mock.patch("quota_butler.handler.push_receipt")
    def test_tomorrow_skip_only_sends_a_light_rest_receipt(self, receipt):
        rc = handler.handle(
            {"action": "tomorrow_skip"},
            config_path=self.config_path,
        )

        self.assertEqual(rc, 0)
        receipt.assert_called_once_with(
            "好的，收到。好好休息，也是在给大脑充电 🌙",
            mock.ANY,
            dry_run=False,
        )
        from quota_butler import state as state_mod
        self.assertIsNone(state_mod.load(self.state_path).active_plan)

    @mock.patch("quota_butler.handler.push_status_card")
    @mock.patch("quota_butler.handler.detect_agents")
    def test_query_uses_classified_agent_status(self, detect, push):
        detect.return_value = {
            "cc": AgentStatus("cc", AgentState.NEEDS_LOGIN, detail="401"),
            "codex": _connected("codex"),
        }

        rc = handler.handle({"action": "query_status"}, config_path=self.config_path)

        self.assertEqual(rc, 0)
        push.assert_called_once()

    @mock.patch("quota_butler.handler.push_status_card")
    @mock.patch("quota_butler.handler.detect_agents")
    def test_query_replies_to_source_chat_when_bridge_provides_chat_id(
        self, detect, push
    ):
        detect.return_value = {"codex": _connected("codex")}

        rc = handler.handle(
            {"action": "query_status", "_chat_id": "oc_direct"},
            config_path=self.config_path,
        )

        self.assertEqual(rc, 0)
        cfg = push.call_args.args[1]
        self.assertEqual(cfg.feishu.chat_id, "oc_direct")
        self.assertEqual(cfg.feishu.user_id, "")
        self.assertEqual(cfg.feishu.message_id, "")

    @mock.patch("quota_butler.handler.push_status_card")
    @mock.patch("quota_butler.handler.detect_agents")
    def test_query_replies_to_source_message_when_bridge_provides_message_id(
        self, detect, push
    ):
        detect.return_value = {"codex": _connected("codex")}

        rc = handler.handle(
            {
                "action": "query_status",
                "_chat_id": "oc_direct",
                "_operator_open_id": "ou_direct",
                "_message_id": "om_direct",
            },
            config_path=self.config_path,
        )

        self.assertEqual(rc, 0)
        cfg = push.call_args.args[1]
        self.assertEqual(cfg.feishu.chat_id, "")
        self.assertEqual(cfg.feishu.user_id, "")
        self.assertEqual(cfg.feishu.message_id, "om_direct")

    @mock.patch("quota_butler.handler.push_command_menu_card")
    def test_menu_replies_to_source_message(self, push):
        rc = handler.handle(
            {
                "action": "menu",
                "_chat_id": "oc_direct",
                "_message_id": "om_direct",
            },
            config_path=self.config_path,
        )

        self.assertEqual(rc, 0)
        cfg = push.call_args.args[0]
        self.assertEqual(cfg.feishu.chat_id, "")
        self.assertEqual(cfg.feishu.user_id, "")
        self.assertEqual(cfg.feishu.message_id, "om_direct")

    @mock.patch("quota_butler.handler.push_status_card")
    @mock.patch("quota_butler.handler.detect_agents")
    def test_query_replies_to_operator_when_bridge_provides_open_id(
        self, detect, push
    ):
        detect.return_value = {"codex": _connected("codex")}

        rc = handler.handle(
            {
                "action": "query_status",
                "_chat_id": "oc_direct",
                "_operator_open_id": "ou_direct",
            },
            config_path=self.config_path,
        )

        self.assertEqual(rc, 0)
        cfg = push.call_args.args[1]
        self.assertEqual(cfg.feishu.chat_id, "")
        self.assertEqual(cfg.feishu.user_id, "ou_direct")
        self.assertEqual(cfg.feishu.message_id, "")


if __name__ == "__main__":
    unittest.main()
