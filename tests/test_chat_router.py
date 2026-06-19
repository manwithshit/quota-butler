import os
import tempfile
import unittest
from unittest import mock

from quota_butler import chat_router
from quota_butler.chat_router import ChatMessage


class TestClassifyIntent(unittest.TestCase):
    def test_recognizes_only_v3_query_and_tomorrow_plan_intents(self):
        self.assertEqual(chat_router.classify_intent("额度"), "query")
        self.assertEqual(
            chat_router.classify_intent("帮我安排明天"),
            "schedule:帮我安排明天",
        )
        self.assertIsNone(chat_router.classify_intent("今天冲刺"))
        self.assertIsNone(chat_router.classify_intent("不断粮模式"))

    def test_recognizes_plan_management_commands(self):
        self.assertEqual(chat_router.classify_intent("查看计划"), "plan:view")
        self.assertEqual(chat_router.classify_intent("取消计划"), "plan:cancel")

    def test_unrelated_message_is_silent(self):
        self.assertIsNone(chat_router.classify_intent("哈哈"))


class TestPendingMessages(unittest.TestCase):
    def test_only_returns_user_text_after_last_seen(self):
        messages = [
            ChatMessage("m3", "额度", "user", "text"),
            ChatMessage("m2", "bot reply", "app", "text"),
            ChatMessage("m1", "哈哈", "user", "text"),
        ]
        pending = chat_router._pending_user_texts(messages, "m1")
        self.assertEqual([message.message_id for message in pending], ["m3"])

    def test_first_poll_only_processes_latest_user_text(self):
        messages = [
            ChatMessage("m3", "额度", "user", "text"),
            ChatMessage("m2", "明天", "user", "text"),
            ChatMessage("m1", "哈哈", "user", "text"),
        ]
        pending = chat_router._pending_user_texts(messages, None)
        self.assertEqual([message.message_id for message in pending], ["m3"])


class TestDispatch(unittest.TestCase):
    @mock.patch("quota_butler.chat_router.handler.handle")
    def test_plan_management_routes_to_handler(self, handle):
        handle.return_value = 0
        self.assertEqual(chat_router._dispatch("plan:view", "/tmp/config.yaml"), 0)
        handle.assert_called_once_with(
            {"action": "view_schedule"},
            config_path="/tmp/config.yaml",
        )


if __name__ == "__main__":
    unittest.main()
