import unittest
from unittest import mock

from quota_butler import query
from quota_butler.agent_status import AgentState, AgentStatus


class TestQuery(unittest.TestCase):
    @mock.patch("quota_butler.query.push_status_card")
    @mock.patch("quota_butler.query.detect_agents")
    @mock.patch("quota_butler.query.config_mod.load")
    def test_query_uses_classified_v3_statuses(self, load, detect, push):
        config = mock.Mock()
        load.return_value = config
        statuses = {
            "cc": AgentStatus("cc", AgentState.NEEDS_LOGIN, detail="401"),
            "codex": AgentStatus("codex", AgentState.NOT_INSTALLED),
        }
        detect.return_value = statuses

        rc = query.run("/tmp/config.yaml")

        self.assertEqual(rc, 0)
        push.assert_called_once_with(statuses, config, dry_run=False)


if __name__ == "__main__":
    unittest.main()
