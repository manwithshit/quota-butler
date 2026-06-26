import unittest
from unittest import mock

from quota_butler.agent_status import AgentState, detect_agents
from quota_butler.providers.base import ProviderError, Usage, WindowUsage


class TestAgentStatus(unittest.TestCase):
    @mock.patch("quota_butler.agent_status.get_provider")
    @mock.patch("quota_butler.agent_status.find_agent_executable")
    def test_installed_agent_with_usage_is_connected(self, find_executable, get_provider):
        find_executable.return_value = "/usr/local/bin/codex"
        usage = Usage("codex", WindowUsage(42, None, 5 * 3600))
        get_provider.return_value.read_usage.return_value = usage

        status = detect_agents(("codex",))["codex"]

        self.assertEqual(status.state, AgentState.CONNECTED)
        self.assertEqual(status.usage, usage)
        self.assertTrue(status.queryable)
        self.assertTrue(status.plan_eligible)

    @mock.patch("quota_butler.agent_status.get_provider")
    @mock.patch("quota_butler.agent_status.find_agent_executable")
    def test_monthly_window_is_queryable_but_not_plan_eligible(
        self, find_executable, get_provider
    ):
        find_executable.return_value = "/usr/local/bin/codex"
        usage = Usage("codex", WindowUsage(42, None, 30 * 86400, "monthly"))
        get_provider.return_value.read_usage.return_value = usage

        status = detect_agents(("codex",))["codex"]

        self.assertEqual(status.state, AgentState.CONNECTED)
        self.assertTrue(status.queryable)
        self.assertFalse(status.plan_eligible)
        self.assertFalse(status.schedulable)

    @mock.patch("quota_butler.agent_status.probe_agent_login", return_value=None)
    @mock.patch("quota_butler.agent_status.get_provider")
    @mock.patch("quota_butler.agent_status.find_agent_executable")
    def test_weekly_exhausted_agent_is_queryable_but_not_schedulable(
        self, find_executable, get_provider, probe
    ):
        find_executable.return_value = "/usr/local/bin/claude"
        usage = Usage(
            "cc",
            WindowUsage(20, None, 5 * 3600, "five_hour"),
            WindowUsage(100, None, 7 * 24 * 3600, "weekly"),
        )
        get_provider.return_value.read_usage.return_value = usage

        status = detect_agents(("cc",))["cc"]

        self.assertEqual(status.state, AgentState.CONNECTED)
        self.assertTrue(status.queryable)
        self.assertFalse(status.plan_eligible)
        self.assertFalse(status.schedulable)

    @mock.patch("quota_butler.agent_status.get_provider")
    @mock.patch("quota_butler.agent_status.find_agent_executable")
    def test_five_hour_exhausted_but_weekly_available_is_still_schedulable(
        self, find_executable, get_provider
    ):
        find_executable.return_value = "/usr/local/bin/codex"
        usage = Usage(
            "codex",
            WindowUsage(100, None, 5 * 3600, "five_hour"),
            WindowUsage(50, None, 7 * 24 * 3600, "weekly"),
        )
        get_provider.return_value.read_usage.return_value = usage

        status = detect_agents(("codex",))["codex"]

        self.assertFalse(status.plan_eligible)
        self.assertTrue(status.schedulable)

    @mock.patch("quota_butler.agent_status.get_provider")
    @mock.patch("quota_butler.agent_status.find_agent_executable")
    def test_missing_executable_is_not_installed(self, find_executable, get_provider):
        find_executable.return_value = None

        status = detect_agents(("cc",))["cc"]

        self.assertEqual(status.state, AgentState.NOT_INSTALLED)
        get_provider.assert_not_called()

    @mock.patch("quota_butler.agent_status.probe_agent_login", return_value=None)
    @mock.patch("quota_butler.agent_status.get_provider")
    @mock.patch("quota_butler.agent_status.find_agent_executable")
    def test_cc_auth_error_after_login_probe_is_token_stale(
        self, find_executable, get_provider, probe
    ):
        # cc 已过 login probe（probe=None=loggedIn:true），此处 auth 失败
        # = 缓存令牌过期可刷新，不是登出，更不是未安装。
        find_executable.return_value = "/usr/local/bin/claude"
        get_provider.return_value.read_usage.side_effect = ProviderError(
            "CC token 已过期"
        )

        status = detect_agents(("cc",))["cc"]

        self.assertEqual(status.state, AgentState.TOKEN_STALE)
        self.assertNotEqual(status.state, AgentState.NOT_INSTALLED)

    @mock.patch("quota_butler.agent_status.get_provider")
    @mock.patch("quota_butler.agent_status.find_agent_executable")
    def test_codex_auth_error_is_needs_login(self, find_executable, get_provider):
        find_executable.return_value = "/usr/local/bin/codex"
        get_provider.return_value.read_usage.side_effect = ProviderError(
            "wham/usage 返回 401：Codex token 失效"
        )

        status = detect_agents(("codex",))["codex"]

        self.assertEqual(status.state, AgentState.NEEDS_LOGIN)

    @mock.patch("quota_butler.agent_status.get_provider")
    @mock.patch("quota_butler.agent_status.probe_agent_login")
    @mock.patch("quota_butler.agent_status.find_agent_executable")
    def test_claude_cli_login_probe_prevents_usage_call_when_logged_out(
        self, find_executable, probe, get_provider
    ):
        find_executable.return_value = "/usr/local/bin/claude"
        probe.return_value = (AgentState.NEEDS_LOGIN, "Claude Code 未登录")

        status = detect_agents(("cc",))["cc"]

        self.assertEqual(status.state, AgentState.NEEDS_LOGIN)
        get_provider.assert_not_called()

    @mock.patch("quota_butler.agent_status.get_provider")
    @mock.patch("quota_butler.agent_status.find_agent_executable")
    def test_network_error_is_temporarily_unavailable(
        self, find_executable, get_provider
    ):
        find_executable.return_value = "/usr/local/bin/codex"
        get_provider.return_value.read_usage.side_effect = ProviderError(
            "wham/usage HTTP 503"
        )

        status = detect_agents(("codex",))["codex"]

        self.assertEqual(status.state, AgentState.UNAVAILABLE)
        self.assertNotEqual(status.state, AgentState.NOT_INSTALLED)


if __name__ == "__main__":
    unittest.main()
