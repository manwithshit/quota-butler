"""S0 对应测试：config 解析（含无 PyYAML 的 fallback）+ state 读写。"""

import os
import tempfile
import unittest

from quota_butler import config as config_mod
from quota_butler import state as state_mod
from quota_butler.state import State

SAMPLE = """\
reset_soon_min: 30
warmup_provider: cc
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
        self.assertEqual(data["feishu"]["chat_id"], "oc_demo123")
        self.assertTrue(data["muted"])


class TestState(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "state.json")
            st = State(last_utilization=42.0, last_notified_reset_at="2026-06-13T12:00:00+00:00")
            state_mod.save(path, st)
            loaded = state_mod.load(path)
            self.assertEqual(loaded.last_utilization, 42.0)
            self.assertEqual(loaded.last_notified_reset_at, "2026-06-13T12:00:00+00:00")

    def test_missing_returns_empty(self):
        st = state_mod.load("/nonexistent/state.json")
        self.assertIsNone(st.last_notified_reset_at)


if __name__ == "__main__":
    unittest.main()
