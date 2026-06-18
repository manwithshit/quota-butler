import unittest
from datetime import datetime, timedelta, timezone

from quota_butler.config import Config, QuietHours
from quota_butler.oneup import should_offer_oneup
from quota_butler.providers.base import Usage, WindowUsage
from quota_butler.state import State


class TestOneUpDecision(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc)
        self.usage = Usage(
            provider="codex",
            five_hour=WindowUsage(utilization=0, resets_at=None, window_seconds=5 * 3600),
        )
        self.previous_reset = (self.now - timedelta(minutes=1)).isoformat()

    def _state(self, **values):
        state = State(
            provider_snapshots={
                "codex": {"utilization": 80, "reset_at": self.previous_reset}
            }
        )
        for key, value in values.items():
            setattr(state, key, value)
        return state

    def test_offers_after_previous_window_has_recovered(self):
        decision = should_offer_oneup(
            "codex", self.usage, self._state(), Config(), now=self.now
        )
        self.assertTrue(decision.notify)
        self.assertEqual(decision.provider, "codex")
        self.assertIn(self.previous_reset, decision.window_key)

    def test_active_plan_suppresses_offer(self):
        decision = should_offer_oneup(
            "codex",
            self.usage,
            self._state(active_plan={"plan_id": "p1", "status": "active"}),
            Config(),
            now=self.now,
        )
        self.assertFalse(decision.notify)

    def test_same_window_is_deduplicated(self):
        key = f"codex:{self.previous_reset}"
        decision = should_offer_oneup(
            "codex",
            self.usage,
            self._state(last_oneup_notified_window=key),
            Config(),
            now=self.now,
        )
        self.assertFalse(decision.notify)

    def test_snooze_and_quiet_hours_suppress_offer(self):
        snoozed = self._state(muted_until=(self.now + timedelta(minutes=30)).isoformat())
        self.assertFalse(
            should_offer_oneup("codex", self.usage, snoozed, Config(), now=self.now).notify
        )
        quiet = Config(quiet_hours=QuietHours("17:00", "19:00"))
        self.assertFalse(
            should_offer_oneup("codex", self.usage, self._state(), quiet, now=self.now).notify
        )

    def test_expired_snooze_reoffers_pending_window(self):
        key = f"codex:{self.previous_reset}"
        state = self._state(
            last_oneup_notified_window=key,
            muted_until=(self.now - timedelta(seconds=1)).isoformat(),
            pending_oneup={"provider": "codex", "window_key": key},
        )
        decision = should_offer_oneup(
            "codex", self.usage, state, Config(), now=self.now
        )
        self.assertTrue(decision.notify)
        self.assertEqual(decision.window_key, key)


if __name__ == "__main__":
    unittest.main()
