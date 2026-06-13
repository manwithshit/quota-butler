"""窗口同一性：覆盖 resets_at 微秒漂移（本机实测同窗口两次读到不同微秒）。"""

import unittest

from quota_butler.window import same_window


class TestSameWindow(unittest.TestCase):
    def test_microsecond_drift_is_same_window(self):
        # 本机真实观测：同一窗口两次读 resets_at 微秒不同
        a = "2026-06-13T11:59:59.959751+00:00"
        b = "2026-06-13T11:59:59.777681+00:00"
        self.assertTrue(same_window(a, b))

    def test_different_windows_not_same(self):
        a = "2026-06-13T11:59:59+00:00"
        b = "2026-06-13T16:59:59+00:00"  # 5h 后，另一个窗口
        self.assertFalse(same_window(a, b))

    def test_none_is_never_same(self):
        self.assertFalse(same_window(None, "2026-06-13T12:00:00+00:00"))
        self.assertFalse(same_window("2026-06-13T12:00:00+00:00", None))

    def test_garbage_is_safe(self):
        self.assertFalse(same_window("not-a-date", "2026-06-13T12:00:00+00:00"))


if __name__ == "__main__":
    unittest.main()
