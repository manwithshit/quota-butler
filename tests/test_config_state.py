"""S0 对应测试：config 解析（含无 PyYAML 的 fallback）+ state 读写。"""

import os
import tempfile
import unittest
from datetime import time as dtime

from quota_butler import config as config_mod
from quota_butler import state as state_mod
from quota_butler.config import QuietHours
from quota_butler.state import State

SAMPLE = """\
reset_soon_min: 30
warmup_provider: cc
scheduler_mode: sustain
scheduler_agents: cc,codex
work_duration_hours: 6
work_start: "08:00"
sleep_time: "23:30"
muted: true
waste_pct: 50
feishu:
  chat_id: oc_demo123
  user_id: ""
"""


class TestConfig(unittest.TestCase):
    def test_parse_sample(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write(SAMPLE)
            path = f.name
        try:
            cfg = config_mod.load(path)
            self.assertEqual(cfg.reset_soon_min, 30)
            self.assertEqual(cfg.warmup_provider, "cc")
            self.assertEqual(cfg.scheduler_mode, "sustain")
            self.assertEqual(cfg.scheduler_agents, "cc,codex")
            self.assertEqual(cfg.work_duration_hours, 6.0)
            self.assertEqual(cfg.work_start, "08:00")
            self.assertEqual(cfg.sleep_time, "23:30")
            self.assertTrue(cfg.muted)
            self.assertEqual(cfg.waste_pct, 50.0)
            self.assertEqual(cfg.feishu.chat_id, "oc_demo123")
        finally:
            os.unlink(path)

    def test_missing_file_uses_defaults(self):
        cfg = config_mod.load("/nonexistent/path/xyz.yaml")
        self.assertEqual(cfg.reset_soon_min, config_mod.DEFAULTS["reset_soon_min"])

    def test_tiny_yaml_fallback_directly(self):
        # 直接打 fallback，证明不依赖 PyYAML
        data = config_mod._tiny_yaml(SAMPLE)
        self.assertEqual(data["reset_soon_min"], 30)
        self.assertEqual(data["scheduler_agents"], "cc,codex")
        self.assertEqual(data["feishu"]["chat_id"], "oc_demo123")
        self.assertTrue(data["muted"])


class TestState(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "state.json")
            st = State(
                last_utilization=42.0,
                last_notified_reset_at="2026-06-13T12:00:00+00:00",
                active_plan={"plan_id": "plan-1", "status": "active"},
            )
            state_mod.save(path, st)
            loaded = state_mod.load(path)
            self.assertEqual(loaded.last_utilization, 42.0)
            self.assertEqual(loaded.last_notified_reset_at, "2026-06-13T12:00:00+00:00")
            self.assertEqual(loaded.active_plan["plan_id"], "plan-1")

    def test_missing_returns_empty(self):
        st = state_mod.load("/nonexistent/state.json")
        self.assertIsNone(st.last_notified_reset_at)


class TestQuietHours(unittest.TestCase):
    def test_same_day_range(self):
        q = QuietHours("01:00", "06:00")
        self.assertTrue(q.contains(dtime(3, 0)))
        self.assertTrue(q.contains(dtime(1, 0)))     # 左闭
        self.assertFalse(q.contains(dtime(6, 0)))    # 右开
        self.assertFalse(q.contains(dtime(7, 0)))

    def test_cross_midnight(self):
        q = QuietHours("23:00", "08:00")
        self.assertTrue(q.contains(dtime(23, 30)))
        self.assertTrue(q.contains(dtime(2, 0)))
        self.assertFalse(q.contains(dtime(9, 0)))
        self.assertFalse(q.contains(dtime(22, 0)))

    def test_disabled_when_empty(self):
        self.assertFalse(QuietHours("", "").contains(dtime(3, 0)))
        self.assertFalse(QuietHours("23:00", "").contains(dtime(23, 30)))

    def test_parsed_from_config(self):
        text = 'reset_soon_min: 20\nquiet_hours:\n  start: "23:00"\n  end: "08:00"\n'
        cfg = config_mod.from_dict(config_mod._tiny_yaml(text))
        self.assertTrue(cfg.quiet_hours.enabled)
        self.assertEqual(cfg.quiet_hours.start, "23:00")
        self.assertTrue(cfg.quiet_hours.contains(dtime(2, 0)))


if __name__ == "__main__":
    unittest.main()
