"""S2 对应测试：三组输入 → 推 / 不推（去重）/ 不推（未临界）。

零依赖，直接 `python -m unittest` 跑。
"""

import unittest
from datetime import datetime, timedelta, timezone

from quota_butler.config import Config
from quota_butler.providers.base import Usage, WindowUsage
from quota_butler.rules import should_notify
from quota_butler.state import State


def make_usage(util, minutes_to_reset, now):
    resets_at = now + timedelta(minutes=minutes_to_reset)
    return Usage(provider="cc",
                 five_hour=WindowUsage(utilization=util, resets_at=resets_at))


class TestShouldNotify(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)
        self.cfg = Config(reset_soon_min=20)

    def test_critical_and_not_notified_yet_notifies(self):
        usage = make_usage(18, minutes_to_reset=10, now=self.now)
        d = should_notify(usage, State(), self.cfg, now=self.now)
        self.assertTrue(d.notify)

    def test_critical_but_already_notified_dedups(self):
        usage = make_usage(18, minutes_to_reset=10, now=self.now)
        st = State(last_notified_reset_at=usage.five_hour.resets_at.isoformat())
        d = should_notify(usage, st, self.cfg, now=self.now)
        self.assertFalse(d.notify)
        self.assertIn("去重", d.reason)

    def test_not_critical_does_not_notify(self):
        usage = make_usage(18, minutes_to_reset=120, now=self.now)
        d = should_notify(usage, State(), self.cfg, now=self.now)
        self.assertFalse(d.notify)

    def test_waste_pct_blocks_when_utilization_high(self):
        cfg = Config(reset_soon_min=20, waste_pct=50)
        usage = make_usage(80, minutes_to_reset=10, now=self.now)
        d = should_notify(usage, State(), cfg, now=self.now)
        self.assertFalse(d.notify)

    def test_waste_pct_allows_when_utilization_low(self):
        cfg = Config(reset_soon_min=20, waste_pct=50)
        usage = make_usage(20, minutes_to_reset=10, now=self.now)
        d = should_notify(usage, State(), cfg, now=self.now)
        self.assertTrue(d.notify)


if __name__ == "__main__":
    unittest.main()
