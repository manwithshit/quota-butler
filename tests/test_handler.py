"""S4 对应测试：回调处理器分支（warmup / skip / 去重 / 未知），全程不真烧 token、不真发飞书。"""

import os
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
