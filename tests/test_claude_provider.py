"""S1-5 容错 + 解析的故障注入测试。

全程 mock subprocess（Keychain）与 urllib（HTTP），**不读真凭据、不打真接口、不烧 token**。
覆盖：Keychain 缺失 / 凭据结构异常 / token 过期 / HTTP 401 / 非 JSON / 缺 five_hour，
以及正常解析路径。关键安全断言：token 过期时**绝不发起网络请求**。
"""

import json
import socket
import unittest
import urllib.error
from datetime import datetime, timezone
from unittest import mock

from quota_butler.providers.base import ProviderError
from quota_butler.providers.claude import ClaudeProvider


def _kc(returncode=0, stdout=""):
    m = mock.Mock()
    m.returncode = returncode
    m.stdout = stdout
    return m


def _good_creds(expires_ms=None):
    if expires_ms is None:
        expires_ms = (datetime.now(timezone.utc).timestamp() + 3600) * 1000
    return json.dumps({"claudeAiOauth": {"accessToken": "tok-abc", "expiresAt": expires_ms}})


def _http_ok(payload):
    cm = mock.MagicMock()
    cm.__enter__.return_value.read.return_value = json.dumps(payload).encode("utf-8")
    return cm


class TestReadTokenErrors(unittest.TestCase):
    @mock.patch("quota_butler.providers.claude.subprocess.run")
    def test_keychain_missing(self, run):
        run.return_value = _kc(returncode=44, stdout="")
        with self.assertRaises(ProviderError) as ctx:
            ClaudeProvider().read_usage()
        self.assertIn("Keychain", str(ctx.exception))

    @mock.patch("quota_butler.providers.claude.subprocess.run")
    def test_bad_credential_json(self, run):
        run.return_value = _kc(stdout="not-json{{")
        with self.assertRaises(ProviderError):
            ClaudeProvider().read_usage()

    @mock.patch("quota_butler.providers.claude.subprocess.run")
    def test_missing_access_token_key(self, run):
        run.return_value = _kc(stdout=json.dumps({"claudeAiOauth": {}}))
        with self.assertRaises(ProviderError):
            ClaudeProvider().read_usage()

    @mock.patch("quota_butler.providers.claude.urllib.request.urlopen")
    @mock.patch("quota_butler.providers.claude.subprocess.run")
    def test_expired_token_does_not_hit_network(self, run, urlopen):
        past_ms = (datetime.now(timezone.utc).timestamp() - 10) * 1000
        run.return_value = _kc(stdout=_good_creds(expires_ms=past_ms))
        with self.assertRaises(ProviderError) as ctx:
            ClaudeProvider().read_usage()
        self.assertIn("过期", str(ctx.exception))
        urlopen.assert_not_called()  # 安全：过期就不该带着 token 去打接口


class TestFetchAndParse(unittest.TestCase):
    @mock.patch("quota_butler.providers.claude.urllib.request.urlopen")
    @mock.patch("quota_butler.providers.claude.subprocess.run")
    def test_http_401(self, run, urlopen):
        run.return_value = _kc(stdout=_good_creds())
        urlopen.side_effect = urllib.error.HTTPError(
            "url", 401, "Unauthorized", {}, None
        )
        with self.assertRaises(ProviderError) as ctx:
            ClaudeProvider().read_usage()
        self.assertIn("401", str(ctx.exception))

    @mock.patch("quota_butler.providers.claude.urllib.request.urlopen")
    @mock.patch("quota_butler.providers.claude.subprocess.run")
    def test_network_error(self, run, urlopen):
        run.return_value = _kc(stdout=_good_creds())
        urlopen.side_effect = urllib.error.URLError("connection refused")
        with self.assertRaises(ProviderError):
            ClaudeProvider().read_usage()

    @mock.patch("quota_butler.providers.claude.urllib.request.urlopen")
    @mock.patch("quota_butler.providers.claude.subprocess.run")
    def test_socket_timeout_is_provider_error(self, run, urlopen):
        run.return_value = _kc(stdout=_good_creds())
        urlopen.side_effect = socket.timeout("read timed out")
        with self.assertRaises(ProviderError) as ctx:
            ClaudeProvider().read_usage()
        self.assertIn("网络错误", str(ctx.exception))

    @mock.patch("quota_butler.providers.claude.urllib.request.urlopen")
    @mock.patch("quota_butler.providers.claude.subprocess.run")
    def test_missing_five_hour(self, run, urlopen):
        run.return_value = _kc(stdout=_good_creds())
        urlopen.return_value = _http_ok({"seven_day": {"utilization": 1, "resets_at": "2026-06-13T12:00:00+00:00"}})
        with self.assertRaises(ProviderError) as ctx:
            ClaudeProvider().read_usage()
        self.assertIn("five_hour", str(ctx.exception))

    @mock.patch("quota_butler.providers.claude.urllib.request.urlopen")
    @mock.patch("quota_butler.providers.claude.subprocess.run")
    def test_happy_path(self, run, urlopen):
        run.return_value = _kc(stdout=_good_creds())
        urlopen.return_value = _http_ok({
            "five_hour": {"utilization": 42.0, "resets_at": "2026-06-13T12:00:00.5+00:00"},
            "seven_day": {"utilization": 61.0, "resets_at": "2026-06-13T18:00:00+00:00"},
        })
        usage = ClaudeProvider().read_usage()
        self.assertEqual(usage.provider, "cc")
        self.assertEqual(usage.five_hour.utilization, 42.0)
        self.assertIsNotNone(usage.five_hour.resets_at.tzinfo)  # 带时区，可做时间差
        self.assertEqual(usage.seven_day.utilization, 61.0)


class TestParseDtRobustness(unittest.TestCase):
    """_parse_dt 抗格式漂移（Z 后缀 / 不同小数秒位数）。"""

    def _parse(self, s):
        from quota_butler.providers.claude import _parse_dt
        dt = _parse_dt(s)
        self.assertIsNotNone(dt.tzinfo)
        return dt

    def test_six_digit_microseconds_real_api_format(self):
        self._parse("2026-06-13T11:59:59.903592+00:00")

    def test_z_suffix(self):
        self._parse("2026-06-13T12:00:00Z")

    def test_one_digit_fraction(self):
        self._parse("2026-06-13T12:00:00.5+00:00")

    def test_nine_digit_fraction_truncates(self):
        self._parse("2026-06-13T12:00:00.123456789+00:00")

    def test_no_fraction(self):
        self._parse("2026-06-13T12:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
