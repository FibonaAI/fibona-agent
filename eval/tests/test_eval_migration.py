import os
import tarfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from plugins.agent import Fibona, ModelSettings
from pydantic import ValidationError
from steps.config import ModelSettings as MakeModelSettings

from core.agent import INSTRUCTIONS


class SharedPolicyTests(unittest.TestCase):
    def test_shared_core_allows_unattended_exit(self):
        self.assertIn("an unattended task is complete and verified", INSTRUCTIONS)
        self.assertIn("Unless a cell ends the run with `quit()`", INSTRUCTIONS)
        self.assertNotIn("Call it only when the user asks.", INSTRUCTIONS)

    def test_make_settings_keep_their_existing_options(self):
        settings = MakeModelSettings(max_output_tokens=1000, context_tokens=2000, extra_body={"test": True})
        self.assertEqual(settings.max_output_tokens, 1000)
        self.assertEqual(
            settings.environment("model", "https://example.invalid")["FIBONA_BASE_URL"], "https://example.invalid"
        )


class ModelSettingsTests(unittest.TestCase):
    def test_environment_ignores_legacy_model_overrides(self):
        with patch.dict(
            os.environ,
            {
                "FIBONA_API_KEY": "test-key",
                "FIBONA_BASE_URL": "https://example.invalid/v1",
                "FIBONA_REASONING_EFFORT": "low",
                "FIBONA_API_RETRIES": "99",
                "FIBONA_MAX_OUTPUT_TOKENS": "1",
                "FIBONA_EXTRA_BODY": "{}",
            },
            clear=True,
        ):
            environment = ModelSettings(reasoning_effort="max", api_retries=3).environment("provider/model")
        self.assertEqual(
            environment,
            {
                "FIBONA_API_KEY": "test-key",
                "FIBONA_BASE_URL": "https://example.invalid/v1",
                "FIBONA_API_RETRIES": "3",
                "FIBONA_MODEL": "provider/model",
            },
        )

    def test_removed_settings_are_rejected(self):
        for name, value in (("max_output_tokens", 1000), ("context_tokens", 2000), ("extra_body", {})):
            with self.subTest(setting=name), self.assertRaises(ValidationError):
                ModelSettings.model_validate({name: value})


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def agent(self, **overrides):
        values = {
            "context_id": "test-session",
            "model_name": "provider/model",
            "model_settings": ModelSettings(reasoning_effort="max", lowest_reasoning_effort="high"),
            "fast": True,
            "ultrafast": False,
            "skills_dir": None,
            "mcp_servers": [],
            "_REPO": Fibona._REPO,
            "exec_as_agent": AsyncMock(),
            "ensure_system_dependencies": AsyncMock(),
        }
        return SimpleNamespace(**(values | overrides))

    async def test_cli_receives_effort_and_tier(self):
        agent = self.agent()
        environment = SimpleNamespace(task_env_config=SimpleNamespace(memory_mb=None))
        await Fibona.run.__wrapped__(agent, "test task", environment, None)
        arguments = agent.exec_as_agent.await_args.kwargs
        self.assertIn("--reasoning-effort max --lowest-reasoning-effort high --fast", arguments["command"])
        self.assertIn("/.venv/bin/python", arguments["command"])
        self.assertIn("/fibona.py --boot-mode", arguments["command"])
        self.assertNotIn("/opt/fibona", arguments["command"])
        self.assertEqual(arguments["env"]["FIBONA_MODEL"], "provider/model")

    async def test_main_bootstrap_still_receives_skills_and_mcp(self):
        server = Mock()
        server.model_dump.return_value = {"name": "test-server", "transport": "stdio", "command": "test-tool"}
        agent = self.agent(skills_dir="/skills", mcp_servers=[server])
        environment = SimpleNamespace(task_env_config=SimpleNamespace(memory_mb=None))
        await Fibona.run.__wrapped__(agent, "test task", environment, None)
        command = agent.exec_as_agent.await_args.kwargs["command"]
        self.assertIn("Task skills are available in /skills", command)
        self.assertIn("test-server", command)
        self.assertIn("test-tool", command)

    async def test_install_uploads_current_source_and_uses_locked_dependencies(self):
        agent = self.agent()
        uploaded_names = []

        async def inspect_upload(source, destination):
            self.assertEqual(destination, "/installed-agent/fibona-source.tar.gz")
            with tarfile.open(source) as archive:
                uploaded_names.extend(archive.getnames())
                uploaded_source = archive.extractfile("core/agent.py").read()
                self.assertEqual(uploaded_source, Path("core/agent.py").read_bytes())

        environment = SimpleNamespace(upload_file=AsyncMock(side_effect=inspect_upload))
        with patch("plugins.agent.configure_package_mirrors", new_callable=AsyncMock) as mirrors:
            await Fibona.install(agent, environment)
        mirrors.assert_awaited_once_with(agent, environment)
        self.assertIn("uv.lock", uploaded_names)
        self.assertIn("pyproject.toml", uploaded_names)
        self.assertFalse(any("__pycache__" in name for name in uploaded_names))
        command = agent.exec_as_agent.await_args.kwargs["command"]
        self.assertIn("--frozen --no-dev", command)
        self.assertNotIn("/opt/fibona", command)
