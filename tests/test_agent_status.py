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
    def test_auth_error_is_needs_login_not_not_installed(
        self, find_executable, get_provider, probe
    ):
        find_executable.return_value = "/usr/local/bin/claude"
        get_provider.return_value.read_usage.side_effect = ProviderError(
            "oauth/usage 返回 401：token 失效"
        )

        status = detect_agents(("cc",))["cc"]

        self.assertEqual(status.state, AgentState.NEEDS_LOGIN)
        self.assertIn("401", status.detail)

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
