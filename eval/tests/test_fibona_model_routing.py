import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from harbor.agents.base import BaseAgent
from plugins.agent import Fibona, ModelSettings


class FibonaModelRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_sent_to_container_is_passed_verbatim(self):
        for supplied, expected in [
            ("chatgpt-api/gpt-6-astra", "chatgpt-api/gpt-6-astra"),
            ("openai/gpt-6-astra", "openai/gpt-6-astra"),
            ("openai/chatgpt-api/gpt-6-astra", "openai/chatgpt-api/gpt-6-astra"),
            ("custom-provider/nested/model", "custom-provider/nested/model"),
            ("gpt-6-astra", "gpt-6-astra"),
            (None, "gpt-6-astra"),
        ]:
            with self.subTest(model=supplied):
                agent = SimpleNamespace(
                    context_id="test-session",
                    model_name=supplied,
                    model_settings=ModelSettings(),
                    fast=False,
                    ultrafast=False,
                    skills_dir=None,
                    mcp_servers=[],
                    _REPO=Fibona._REPO,
                    exec_as_agent=AsyncMock(),
                )
                BaseAgent._init_model_info(agent)
                environment = SimpleNamespace(task_env_config=SimpleNamespace(memory_mb=None))
                await Fibona.run.__wrapped__(agent, "test task", environment, None)
                self.assertEqual(agent.exec_as_agent.await_args.kwargs["env"]["FIBONA_MODEL"], expected)


if __name__ == "__main__":
    unittest.main()
