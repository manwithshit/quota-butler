"""群聊查询测试：多 provider 聚合，读不到的不崩、照样发卡。

全程 mock provider + push，不读真凭据、不发真飞书。
"""

import unittest
from datetime import datetime, timezone
from unittest import mock

from quota_butler import query
from quota_butler.providers.base import ProviderError, Usage, WindowUsage


def _usage(name, util):
    return Usage(
        provider=name,
        five_hour=WindowUsage(util, datetime(2026, 6, 14, tzinfo=timezone.utc), 18000),
    )


class TestQuery(unittest.TestCase):
    @mock.patch("quota_butler.query.push_status_card")
    @mock.patch("quota_butler.query.get_provider")
    @mock.patch("quota_butler.query.config_mod.load")
    def test_both_ok(self, load, get_provider, push):
        load.return_value = mock.Mock()
        get_provider.side_effect = lambda name: mock.Mock(
            read_usage=mock.Mock(return_value=_usage(name, 50))
        )
        rc = query.run("x", dry_run=True)
        self.assertEqual(rc, 0)
        push.assert_called_once()
        results = push.call_args[0][0]
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r[1] is not None for r in results))

    @mock.patch("quota_butler.query.push_status_card")
    @mock.patch("quota_butler.query.get_provider")
    @mock.patch("quota_butler.query.config_mod.load")
    def test_one_fails_still_pushes(self, load, get_provider, push):
        load.return_value = mock.Mock()

        def gp(name):
            p = mock.Mock()
            if name == "codex":
                p.read_usage.side_effect = ProviderError("auth.json 不存在")
            else:
                p.read_usage.return_value = _usage(name, 30)
            return p

        get_provider.side_effect = gp
        rc = query.run("x", dry_run=True)
        self.assertEqual(rc, 0)                       # 一个失败不影响整体
        results = push.call_args[0][0]
        errs = [r for r in results if r[1] is None]
        self.assertEqual(len(errs), 1)
        self.assertIn("不存在", errs[0][2])


if __name__ == "__main__":
    unittest.main()
