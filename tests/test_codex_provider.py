"""Codex provider 感知测试。用本机实测 dump 的真实 JSON 当 fixture。

全程 mock urllib（HTTP）+ 临时 auth.json，不读真凭据、不打真接口、不烧额度。
"""

import json
import os
import tempfile
import unittest
import urllib.error
from unittest import mock

from quota_butler.providers.base import ProviderError
from quota_butler.providers.codex import CodexProvider

# 本机实测 dump（免费档：primary 是月度额度，secondary 为 null）
FREE_USAGE = {
    "plan_type": "free",
    "rate_limit": {
        "allowed": False,
        "limit_reached": True,
        "primary_window": {
            "used_percent": 100,
            "limit_window_seconds": 2592000,
            "reset_after_seconds": 1474378,
            "reset_at": 1782898200,
        },
        "secondary_window": None,
    },
}

# 构造一个付费档样例（primary=5h、secondary=7天）
PAID_USAGE = {
    "plan_type": "plus",
    "rate_limit": {
        "primary_window": {"used_percent": 40, "reset_at": 1782898200, "limit_window_seconds": 18000},
        "secondary_window": {"used_percent": 55, "reset_at": 1783000000, "limit_window_seconds": 604800},
    },
}


def _http_ok(payload):
    cm = mock.MagicMock()
    cm.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
    return cm


class TestCodexRead(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.auth = os.path.join(self.tmp, "auth.json")
        with open(self.auth, "w", encoding="utf-8") as f:
            json.dump({"tokens": {"access_token": "cx-tok", "account_id": "acc-1"}}, f)

    @mock.patch("quota_butler.providers.codex.urllib.request.urlopen")
    def test_free_monthly_parse(self, urlopen):
        urlopen.return_value = _http_ok(FREE_USAGE)
        with mock.patch("quota_butler.providers.codex.AUTH_PATH", self.auth):
            usage = CodexProvider().read_usage()
        self.assertEqual(usage.provider, "codex")
        self.assertEqual(usage.five_hour.utilization, 100.0)
        self.assertEqual(usage.five_hour.window_seconds, 2592000)   # 月度
        self.assertIsNotNone(usage.five_hour.resets_at.tzinfo)
        self.assertEqual(int(usage.five_hour.resets_at.timestamp()), 1782898200)
        self.assertIsNone(usage.seven_day)                          # 免费档无 secondary

    @mock.patch("quota_butler.providers.codex.urllib.request.urlopen")
    def test_paid_dual_window(self, urlopen):
        urlopen.return_value = _http_ok(PAID_USAGE)
        with mock.patch("quota_butler.providers.codex.AUTH_PATH", self.auth):
            usage = CodexProvider().read_usage()
        self.assertEqual(usage.five_hour.window_seconds, 18000)     # 5h
        self.assertEqual(usage.seven_day.utilization, 55.0)
        self.assertEqual(usage.seven_day.window_seconds, 604800)    # 7天

    @mock.patch("quota_butler.providers.codex.urllib.request.urlopen")
    def test_zero_usage_null_reset_at(self, urlopen):
        payload = {
            "plan_type": "plus",
            "rate_limit": {
                "primary_window": {
                    "used_percent": 0,
                    "reset_at": None,
                    "limit_window_seconds": 18000,
                },
                "secondary_window": None,
            },
        }
        urlopen.return_value = _http_ok(payload)
        with mock.patch("quota_butler.providers.codex.AUTH_PATH", self.auth):
            usage = CodexProvider().read_usage()
        self.assertEqual(usage.five_hour.utilization, 0.0)
        self.assertIsNone(usage.five_hour.resets_at)
        self.assertEqual(usage.five_hour.window_seconds, 18000)

    @mock.patch("quota_butler.providers.codex.subprocess.run")
    @mock.patch("quota_butler.providers.codex.urllib.request.urlopen")
    def test_read_usage_401_auto_heal_success(self, urlopen, run):
        # 首次调用返回 401，刷新 token 后重试返回正确数据
        urlopen.side_effect = [
            urllib.error.HTTPError("u", 401, "unauthorized", {}, None),
            _http_ok(PAID_USAGE)
        ]
        run.return_value = mock.MagicMock(returncode=0, stdout="pong")

        with mock.patch("quota_butler.providers.codex.AUTH_PATH", self.auth):
            usage = CodexProvider().read_usage()

        self.assertEqual(usage.five_hour.window_seconds, 18000)
        run.assert_called_once_with(["codex", "exec", "ping"], capture_output=True, text=True, timeout=45)

    @mock.patch("quota_butler.providers.codex.subprocess.run")
    @mock.patch("quota_butler.providers.codex.urllib.request.urlopen")
    def test_read_usage_401_auto_heal_failed(self, urlopen, run):
        # 两次调用均返回 401
        urlopen.side_effect = [
            urllib.error.HTTPError("u", 401, "unauthorized", {}, None),
            urllib.error.HTTPError("u", 401, "unauthorized", {}, None)
        ]
        run.return_value = mock.MagicMock(returncode=0, stdout="pong")

        with mock.patch("quota_butler.providers.codex.AUTH_PATH", self.auth):
            with self.assertRaises(ProviderError) as ctx:
                CodexProvider().read_usage()

        self.assertIn("401", str(ctx.exception))
        self.assertEqual(urlopen.call_count, 2)
        run.assert_called_once()

    @mock.patch("quota_butler.providers.codex.urllib.request.urlopen")
    def test_read_usage_retries_transient_server_error(self, urlopen):
        urlopen.side_effect = [
            urllib.error.HTTPError("u", 500, "server error", {}, None),
            _http_ok(PAID_USAGE),
        ]

        with mock.patch("quota_butler.providers.codex.AUTH_PATH", self.auth):
            usage = CodexProvider().read_usage()

        self.assertEqual(usage.five_hour.utilization, 40.0)
        self.assertEqual(urlopen.call_count, 2)

    def test_auth_missing(self):
        with mock.patch("quota_butler.providers.codex.AUTH_PATH", "/nonexistent/auth.json"):
            with self.assertRaises(ProviderError) as ctx:
                CodexProvider().read_usage()
        self.assertIn("不存在", str(ctx.exception))

    @mock.patch("quota_butler.providers.codex.subprocess.run")
    def test_warmup_success(self, run):
        run.return_value = mock.MagicMock(returncode=0, stdout="Success warmup response")
        res = CodexProvider().warmup("hello")
        self.assertEqual(res, "Success warmup response")
        run.assert_called_once_with(["codex", "exec", "hello"], capture_output=True, text=True, timeout=120)

    @mock.patch("quota_butler.providers.codex.subprocess.run")
    def test_warmup_failure(self, run):
        run.return_value = mock.MagicMock(returncode=1, stderr="Subprocess error")
        with self.assertRaises(ProviderError) as ctx:
            CodexProvider().warmup("hello")
        self.assertIn("退出码 1", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
