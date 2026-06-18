"""S4 对应测试：回调处理器分支（warmup / skip / 去重 / 未知），全程不真烧 token、不真发飞书。"""

import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

from quota_butler import handler
from quota_butler import config as config_mod


def _config_file(chat_id="oc_test"):
    d = tempfile.mkdtemp()
    cfg_path = os.path.join(d, "config.yaml")
    state_path = os.path.join(d, "state.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(
            "warmup_provider: cc\n"
            "warmup_prompt: say hi\n"
            f"state_path: {state_path}\n"
            "feishu:\n"
            f"  chat_id: {chat_id}\n"
        )
    return cfg_path, state_path


def _guided_generate_payload():
    return {
        "action": "schedule_flow",
        "flow_version": 2,
        "step": "generate",
        "target_date": (date.today() + timedelta(days=1)).isoformat(),
        "preferences": {
            "task_type": "coding",
            "intensity": "normal",
            "work_start": "09:00",
            "work_end": "17:00",
        },
    }


class TestHandler(unittest.TestCase):
    def setUp(self):
        self.cfg_path, self.state_path = _config_file()

    @mock.patch("quota_butler.handler.push_receipt")
    @mock.patch("quota_butler.handler.get_provider")
    def test_warmup_calls_provider_and_records_state(self, get_provider, push_receipt):
        fake = mock.Mock()
        fake.warmup.return_value = "hi"
        get_provider.return_value = fake

        rc = handler.handle(
            {"action": "warmup", "resets_at": "2026-06-13T12:00:00+00:00"},
            config_path=self.cfg_path,
        )
        self.assertEqual(rc, 0)
        get_provider.assert_called_once_with("codex")
        fake.warmup.assert_called_once_with("say hi")
        push_receipt.assert_called_once()
        self.assertIn("已开窗", push_receipt.call_args[0][0])

        from quota_butler import state as state_mod
        st = state_mod.load(self.state_path)
        self.assertEqual(st.last_warmed_reset_at, "2026-06-13T12:00:00+00:00")

    @mock.patch("quota_butler.handler.push_receipt")
    @mock.patch("quota_butler.handler.get_provider")
    def test_dedup_skips_second_warmup(self, get_provider, push_receipt):
        fake = mock.Mock()
        get_provider.return_value = fake
        payload = {"action": "warmup", "resets_at": "2026-06-13T12:00:00+00:00"}

        handler.handle(payload, config_path=self.cfg_path)        # 第一次
        get_provider.reset_mock(); fake.reset_mock()
        rc = handler.handle(payload, config_path=self.cfg_path)    # 第二次同窗口

        self.assertEqual(rc, 0)
        fake.warmup.assert_not_called()                            # 没再烧 token

    @mock.patch("quota_butler.handler.push_receipt")
    @mock.patch("quota_butler.handler.get_provider")
    def test_skip_is_silent_no_warmup(self, get_provider, push_receipt):
        rc = handler.handle({"action": "skip"}, config_path=self.cfg_path)
        self.assertEqual(rc, 0)
        get_provider.assert_not_called()
        push_receipt.assert_not_called()

    def test_unknown_action_returns_nonzero(self):
        rc = handler.handle({"action": "bogus"}, config_path=self.cfg_path)
        self.assertEqual(rc, 1)

    @mock.patch("quota_butler.handler.push_guided_schedule_card")
    @mock.patch("quota_butler.handler.build_schedule_task_card")
    @mock.patch("quota_butler.handler.get_provider")
    @mock.patch("quota_butler.handler.plan_from_config")
    def test_schedule_intent_starts_guided_flow_for_tomorrow(
        self, plan_from_config, get_provider, build_task, push
    ):
        card = {"schema": "2.0"}
        build_task.return_value = card

        rc = handler.handle(
            {"action": "schedule_intent", "intent": "帮我安排明天"},
            config_path=self.cfg_path,
        )

        self.assertEqual(rc, 0)
        self.assertEqual(
            build_task.call_args.args[0],
            date.today() + timedelta(days=1),
        )
        push.assert_called_once_with(
            card,
            mock.ANY,
            dry_run=False,
        )
        get_provider.assert_not_called()
        plan_from_config.assert_not_called()

    @mock.patch("quota_butler.handler.push_guided_schedule_card")
    @mock.patch("quota_butler.handler.build_schedule_intensity_card")
    def test_schedule_flow_advances_to_requested_step(self, build_card, push):
        card = {"schema": "2.0"}
        build_card.return_value = card
        rc = handler.handle(
            {
                "action": "schedule_flow",
                "flow_version": 2,
                "step": "intensity",
                "target_date": (date.today() + timedelta(days=1)).isoformat(),
                "preferences": {
                    "task_type": "coding",
                    "intensity": "normal",
                    "work_start": "09:00",
                    "work_end": "17:00",
                },
            },
            config_path=self.cfg_path,
        )

        self.assertEqual(rc, 0)
        prefs = build_card.call_args.args[1]
        self.assertEqual(prefs.task_type, "coding")
        push.assert_called_once_with(card, mock.ANY, dry_run=False)

    @mock.patch("quota_butler.handler.push_guided_schedule_card")
    @mock.patch("quota_butler.handler.build_schedule_summary_card")
    def test_schedule_flow_reads_time_form_values(self, build_card, push):
        card = {"schema": "2.0"}
        build_card.return_value = card

        rc = handler.handle(
            {
                "action": "schedule_flow",
                "flow_version": 2,
                "step": "summary",
                "target_date": (date.today() + timedelta(days=1)).isoformat(),
                "preferences": {
                    "task_type": "research",
                    "intensity": "high",
                    "work_start": "09:00",
                    "work_end": "17:00",
                },
                "form_value": {
                    "work_start": "10:15",
                    "work_end": "18:45",
                },
            },
            config_path=self.cfg_path,
        )

        self.assertEqual(rc, 0)
        prefs = build_card.call_args.args[1]
        self.assertEqual(prefs.work_start, "10:15")
        self.assertEqual(prefs.work_end, "18:45")
        push.assert_called_once()

    @mock.patch("quota_butler.handler.push_guided_schedule_card")
    @mock.patch("quota_butler.handler.build_schedule_time_card")
    def test_schedule_flow_invalid_time_returns_time_card_with_error(
        self, build_card, push
    ):
        card = {"schema": "2.0"}
        build_card.return_value = card

        rc = handler.handle(
            {
                "action": "schedule_flow",
                "flow_version": 2,
                "step": "summary",
                "target_date": (date.today() + timedelta(days=1)).isoformat(),
                "preferences": {
                    "task_type": "research",
                    "intensity": "high",
                    "work_start": "09:00",
                    "work_end": "17:00",
                },
                "form_value": {
                    "work_start": "18:00",
                    "work_end": "09:00",
                },
            },
            config_path=self.cfg_path,
        )

        self.assertEqual(rc, 0)
        self.assertIn("晚于", build_card.call_args.kwargs["error"])
        push.assert_called_once()

    @mock.patch("quota_butler.handler.push_receipt")
    @mock.patch("quota_butler.handler.push_guided_schedule_card")
    def test_schedule_flow_rejects_old_or_expired_card(self, push, push_receipt):
        rc = handler.handle(
            {
                "action": "schedule_flow",
                "flow_version": 1,
                "step": "task",
                "target_date": date.today().isoformat(),
                "preferences": {},
            },
            config_path=self.cfg_path,
        )

        self.assertEqual(rc, 4)
        push.assert_not_called()
        self.assertIn("重新规划", push_receipt.call_args.args[0])

    @mock.patch("quota_butler.handler.push_schedule_card")
    @mock.patch("quota_butler.handler.plan_from_preferences")
    @mock.patch("quota_butler.handler.get_provider")
    def test_schedule_generation_uses_codex_when_claude_is_unavailable(
        self, get_provider, plan_from_preferences, push
    ):
        plan_from_preferences.return_value = mock.Mock()

        def provider(name):
            item = mock.Mock()
            if name == "cc":
                item.read_usage.side_effect = handler.ProviderError("expired")
            return item

        get_provider.side_effect = provider

        rc = handler.handle(
            _guided_generate_payload(),
            config_path=self.cfg_path,
        )

        self.assertEqual(rc, 0)
        self.assertEqual(plan_from_preferences.call_args.kwargs["agents"], ("codex",))
        push.assert_called_once()

    @mock.patch("quota_butler.handler.push_schedule_card")
    @mock.patch("quota_butler.handler.plan_from_preferences")
    @mock.patch("quota_butler.handler.get_provider")
    def test_schedule_generation_uses_claude_when_codex_is_unavailable(
        self, get_provider, plan_from_preferences, push
    ):
        plan_from_preferences.return_value = mock.Mock()

        def provider(name):
            item = mock.Mock()
            if name == "codex":
                item.read_usage.side_effect = handler.ProviderError("offline")
            return item

        get_provider.side_effect = provider

        rc = handler.handle(
            _guided_generate_payload(),
            config_path=self.cfg_path,
        )

        self.assertEqual(rc, 0)
        self.assertEqual(plan_from_preferences.call_args.kwargs["agents"], ("cc",))
        push.assert_called_once()

    @mock.patch("quota_butler.handler.push_schedule_card")
    @mock.patch("quota_butler.handler.plan_from_preferences")
    @mock.patch("quota_butler.handler.get_provider")
    def test_schedule_generation_combines_both_available_agents(
        self, get_provider, plan_from_preferences, push
    ):
        plan_from_preferences.return_value = mock.Mock()

        rc = handler.handle(
            _guided_generate_payload(),
            config_path=self.cfg_path,
        )

        self.assertEqual(rc, 0)
        self.assertEqual(
            plan_from_preferences.call_args.kwargs["agents"],
            ("cc", "codex"),
        )
        push.assert_called_once()

    @mock.patch("quota_butler.handler.push_receipt")
    @mock.patch("quota_butler.handler.push_schedule_card")
    @mock.patch("quota_butler.handler.plan_from_preferences")
    @mock.patch("quota_butler.handler.get_provider")
    def test_schedule_generation_stops_only_when_all_agents_are_unavailable(
        self, get_provider, plan_from_preferences, push, push_receipt
    ):
        get_provider.return_value.read_usage.side_effect = handler.ProviderError(
            "unavailable"
        )

        rc = handler.handle(
            _guided_generate_payload(),
            config_path=self.cfg_path,
        )

        self.assertEqual(rc, 4)
        plan_from_preferences.assert_not_called()
        push.assert_not_called()
        message = push_receipt.call_args.args[0]
        self.assertIn("暂时没有可用 Agent", message)
        self.assertNotIn("unavailable", message)

    @mock.patch("quota_butler.handler.push_receipt")
    @mock.patch("quota_butler.handler.get_provider")
    @mock.patch("quota_butler.handler.install_plan_tasks")
    def test_adopt_schedule_installs_tasks_and_records_active_plan(
        self, install, get_provider, push_receipt
    ):
        get_provider.return_value.read_usage.return_value = mock.Mock()
        install.return_value = [
            {"label": "com.quota-butler.plan.p1.0", "plist_path": "/tmp/p1.plist"}
        ]
        payload = {
            "action": "adopt_schedule",
            "plan": {
                "plan_version": 2,
                "plan_id": "p1",
                "mode": "balanced",
                "agents": ["cc", "codex"],
                "work_start": "2026-06-19T09:00:00+00:00",
                "work_end": "2026-06-19T17:00:00+00:00",
                "cas": 1.0,
                "waiting_minutes": 0,
                "events": [
                    {
                        "agent": "codex",
                        "kind": "warmup",
                        "at": "2026-06-19T08:30:00+00:00",
                    }
                ],
            },
        }

        rc = handler.handle(payload, config_path=self.cfg_path)

        self.assertEqual(rc, 0)
        from quota_butler import state as state_mod
        active = state_mod.load(self.state_path).active_plan
        self.assertEqual(active["plan_id"], "p1")
        self.assertEqual(active["status"], "active")
        self.assertEqual(len(active["tasks"]), 1)
        self.assertEqual(install.call_args.kwargs["config_path"], self.cfg_path)
        push_receipt.assert_called_once()
        self.assertIn("已采用计划", push_receipt.call_args.args[0])

    @mock.patch("quota_butler.handler.push_receipt")
    @mock.patch("quota_butler.handler.get_provider")
    @mock.patch("quota_butler.handler.install_plan_tasks")
    def test_adopt_schedule_rejects_plan_with_unavailable_agent(
        self, install, get_provider, push_receipt
    ):
        get_provider.return_value.read_usage.side_effect = handler.ProviderError(
            "token expired"
        )
        payload = {
            "action": "adopt_schedule",
            "plan": {
                "plan_version": 2,
                "plan_id": "p1",
                "mode": "balanced",
                "agents": ["cc"],
                "work_start": "2026-06-19T09:00:00+00:00",
                "work_end": "2026-06-19T17:00:00+00:00",
                "events": [
                    {
                        "agent": "cc",
                        "kind": "warmup",
                        "at": "2026-06-19T08:30:00+00:00",
                    }
                ],
            },
        }

        rc = handler.handle(payload, config_path=self.cfg_path)

        self.assertEqual(rc, 4)
        install.assert_not_called()
        self.assertIn("Claude Code", push_receipt.call_args.args[0])
        self.assertIn("不可用", push_receipt.call_args.args[0])

    @mock.patch("quota_butler.handler.push_receipt")
    @mock.patch("quota_butler.handler.get_provider")
    @mock.patch("quota_butler.handler.install_plan_tasks")
    def test_adopt_schedule_rejects_legacy_plan(
        self, install, get_provider, push_receipt
    ):
        install.return_value = []
        get_provider.return_value.read_usage.return_value = mock.Mock()
        payload = {
            "action": "adopt_schedule",
            "plan": {
                "plan_id": "legacy",
                "mode": "balanced",
                "agents": ["codex"],
                "work_start": "2026-06-19T09:00:00+00:00",
                "work_end": "2026-06-19T17:00:00+00:00",
                "events": [
                    {
                        "agent": "codex",
                        "kind": "warmup",
                        "at": "2026-06-19T08:30:00+00:00",
                    }
                ],
            },
        }

        rc = handler.handle(payload, config_path=self.cfg_path)

        self.assertEqual(rc, 4)
        get_provider.assert_not_called()
        install.assert_not_called()
        self.assertIn("旧计划已失效", push_receipt.call_args.args[0])
        self.assertIn("重新规划", push_receipt.call_args.args[0])

    @mock.patch("quota_butler.handler.push_receipt")
    @mock.patch("quota_butler.handler.cancel_plan_tasks")
    def test_cancel_schedule_removes_tasks_and_clears_state(self, cancel, push_receipt):
        from quota_butler import state as state_mod
        state_mod.save(
            self.state_path,
            state_mod.State(
                active_plan={
                    "plan_id": "p1",
                    "status": "active",
                    "tasks": [{"label": "task", "plist_path": "/tmp/task.plist"}],
                }
            ),
        )

        rc = handler.handle({"action": "cancel_schedule"}, config_path=self.cfg_path)

        self.assertEqual(rc, 0)
        self.assertIsNone(state_mod.load(self.state_path).active_plan)
        cancel.assert_called_once()
        self.assertIn("已取消计划", push_receipt.call_args.args[0])

    @mock.patch("quota_butler.handler.push_receipt")
    @mock.patch("quota_butler.handler.install_plan_tasks")
    def test_adopt_schedule_does_not_silently_replace_active_plan(self, install, push_receipt):
        from quota_butler import state as state_mod
        state_mod.save(
            self.state_path,
            state_mod.State(active_plan={"plan_id": "existing", "status": "active"}),
        )

        rc = handler.handle(
            {"action": "adopt_schedule", "plan": {"plan_id": "new"}},
            config_path=self.cfg_path,
        )

        self.assertEqual(rc, 4)
        install.assert_not_called()
        self.assertEqual(state_mod.load(self.state_path).active_plan["plan_id"], "existing")
        self.assertIn("已有生效计划", push_receipt.call_args.args[0])

    @mock.patch("quota_butler.handler.cancel_plan_tasks")
    @mock.patch("quota_butler.handler.push_receipt")
    @mock.patch("quota_butler.handler.get_provider")
    def test_scheduled_warmup_uses_plan_provider_and_marks_task_executed(
        self, get_provider, push_receipt, cancel
    ):
        from quota_butler import state as state_mod
        task = {
            "label": "task",
            "plist_path": "/tmp/task.plist",
            "scheduled_for": "2026-06-19T08:30:00+00:00",
            "provider": "codex",
        }
        state_mod.save(
            self.state_path,
            state_mod.State(
                active_plan={
                    "plan_id": "p1",
                    "status": "active",
                    "tasks": [task],
                }
            ),
        )
        provider = mock.Mock()
        get_provider.return_value = provider

        rc = handler.handle(
            {
                "action": "scheduled_warmup",
                "plan_id": "p1",
                "provider": "codex",
                "scheduled_for": "2026-06-19T08:30:00+00:00",
            },
            config_path=self.cfg_path,
        )

        self.assertEqual(rc, 0)
        get_provider.assert_called_once_with("codex")
        provider.warmup.assert_called_once_with("say hi")
        self.assertEqual(state_mod.load(self.state_path).active_plan["tasks"][0]["status"], "executed")
        cancel.assert_called_once()
        self.assertIn("Codex", push_receipt.call_args.args[0])

    @mock.patch("quota_butler.handler.get_provider")
    def test_scheduled_warmup_rejects_task_not_in_active_plan(self, get_provider):
        from quota_butler import state as state_mod
        state_mod.save(
            self.state_path,
            state_mod.State(active_plan={"plan_id": "p1", "status": "active", "tasks": []}),
        )

        rc = handler.handle(
            {
                "action": "scheduled_warmup",
                "plan_id": "p1",
                "provider": "codex",
                "scheduled_for": "2026-06-19T08:30:00+00:00",
            },
            config_path=self.cfg_path,
        )

        self.assertEqual(rc, 4)
        get_provider.assert_not_called()

    @mock.patch("quota_butler.handler.push_receipt")
    @mock.patch("quota_butler.handler.get_provider")
    def test_scheduled_warmup_rejects_non_codex_provider(
        self, get_provider, push_receipt
    ):
        from quota_butler import state as state_mod
        task = {
            "scheduled_for": "2026-06-19T08:30:00+00:00",
            "provider": "cc",
        }
        state_mod.save(
            self.state_path,
            state_mod.State(
                active_plan={
                    "plan_id": "p1",
                    "status": "active",
                    "tasks": [task],
                }
            ),
        )

        rc = handler.handle(
            {
                "action": "scheduled_warmup",
                "plan_id": "p1",
                "provider": "cc",
                "scheduled_for": "2026-06-19T08:30:00+00:00",
            },
            config_path=self.cfg_path,
        )

        self.assertEqual(rc, 4)
        get_provider.assert_not_called()
        self.assertIn("仅允许 Codex", push_receipt.call_args.args[0])

    @mock.patch("quota_butler.handler.push_receipt")
    @mock.patch("quota_butler.handler.get_provider")
    def test_oneup_start_rejects_non_codex_provider(
        self, get_provider, push_receipt
    ):
        rc = handler.handle(
            {"action": "oneup_start", "provider": "cc"},
            config_path=self.cfg_path,
        )

        self.assertEqual(rc, 4)
        get_provider.assert_not_called()
        self.assertIn("仅允许 Codex", push_receipt.call_args.args[0])

    @mock.patch("quota_butler.handler.push_receipt")
    def test_oneup_snooze_sets_muted_until(self, push_receipt):
        from quota_butler import state as state_mod

        rc = handler.handle(
            {
                "action": "oneup_snooze",
                "minutes": 30,
                "provider": "codex",
                "window_key": "codex:2026-06-18T09:59:00+00:00",
            },
            config_path=self.cfg_path,
        )

        self.assertEqual(rc, 0)
        state = state_mod.load(self.state_path)
        self.assertIsNotNone(state.muted_until)
        self.assertEqual(state.pending_oneup["provider"], "codex")
        self.assertIn("30 分钟", push_receipt.call_args.args[0])

    @mock.patch("quota_butler.handler.push_receipt")
    @mock.patch("quota_butler.handler.get_provider")
    def test_oneup_start_warms_selected_provider(self, get_provider, push_receipt):
        provider = mock.Mock()
        get_provider.return_value = provider

        rc = handler.handle(
            {
                "action": "oneup_start",
                "provider": "codex",
                "window_key": "codex:2026-06-18T09:59:00+00:00",
            },
            config_path=self.cfg_path,
        )

        self.assertEqual(rc, 0)
        get_provider.assert_called_once_with("codex")
        provider.warmup.assert_called_once_with("say hi")
        self.assertIn("Codex", push_receipt.call_args.args[0])

        rc = handler.handle(
            {
                "action": "oneup_start",
                "provider": "codex",
                "window_key": "codex:2026-06-18T09:59:00+00:00",
            },
            config_path=self.cfg_path,
        )
        self.assertEqual(rc, 0)
        provider.warmup.assert_called_once_with("say hi")
        self.assertIn("已经启动过", push_receipt.call_args.args[0])

    @mock.patch("quota_butler.handler.push_receipt")
    @mock.patch("quota_butler.handler.get_provider")
    def test_oneup_start_dry_run_never_warms_provider(self, get_provider, push_receipt):
        rc = handler.handle(
            {"action": "oneup_start", "provider": "codex"},
            config_path=self.cfg_path,
            dry_run=True,
        )
        self.assertEqual(rc, 0)
        get_provider.assert_not_called()

    @mock.patch("quota_butler.handler.handle")
    def test_main_accepts_config_option_before_payload(self, handle):
        handle.return_value = 0
        rc = handler.main([
            "--config",
            self.cfg_path,
            '{"action":"skip"}',
        ])
        self.assertEqual(rc, 0)
        handle.assert_called_once_with(
            {"action": "skip"},
            config_path=self.cfg_path,
            dry_run=False,
        )

    @mock.patch("quota_butler.handler.push_receipt")
    def test_oneup_mute_today_sets_next_local_midnight(self, push_receipt):
        from quota_butler import state as state_mod

        rc = handler.handle(
            {"action": "oneup_mute_today"},
            config_path=self.cfg_path,
        )

        self.assertEqual(rc, 0)
        muted_until = datetime.fromisoformat(state_mod.load(self.state_path).muted_until)
        self.assertGreater(muted_until, datetime.now(timezone.utc))
        self.assertIn("今天不再提醒", push_receipt.call_args.args[0])

    @mock.patch("quota_butler.handler.push_receipt")
    @mock.patch("quota_butler.handler.get_provider")
    def test_warmup_failure_sends_error_receipt(self, get_provider, push_receipt):
        # S4-6：预热失败 → 返回非零 + 发「❌ 失败」回执，不吞错
        from quota_butler.providers.base import ProviderError
        fake = mock.Mock()
        fake.warmup.side_effect = ProviderError("claude -p 退出码 1: boom")
        get_provider.return_value = fake

        rc = handler.handle(
            {"action": "warmup", "resets_at": "2026-06-13T12:00:00+00:00"},
            config_path=self.cfg_path,
        )
        self.assertEqual(rc, 3)
        push_receipt.assert_called_once()
        self.assertIn("❌", push_receipt.call_args[0][0])

        # 失败后不应记成"已预热"，以便后续可重试
        from quota_butler import state as state_mod
        st = state_mod.load(self.state_path)
        self.assertIsNone(st.last_warmed_reset_at)


if __name__ == "__main__":
    unittest.main()
