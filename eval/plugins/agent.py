"""Harbor installed agent: runs Fibona with bootstrap.py once per task inside the task container.

Supports reasoning_effort medium/high/xhigh/max; the core enforces at least medium.
This implementation has no fixed context-window policy and does not consume context_tokens.

steps/config.py mounts /opt/fibona (wheels, uv and Python, copied by job.py), so install runs offline.
After the run, Fibona's run logs become agent/trajectory.json (ATIF), which `harbor view` shows; see plugins/utils.py.
"""

import json
import shlex
from pathlib import Path

from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.utils.trajectory_utils import format_trajectory_json
from plugins.utils import convert
from steps.config import ModelSettings

# The bootstrap cell each task runs; see bootstrap.py.
BOOTSTRAP = (Path(__file__).parent / "bootstrap.py").read_text()


class Fibona(BaseInstalledAgent):
    capabilities = AgentCapabilities(skills=True, mcp_servers=True)

    def __init__(self, *args, base_url: str | None = None, **kwargs) -> None:
        self.base_url = base_url
        self.model_settings = ModelSettings.model_validate(
            {key: kwargs.pop(key) for key in ModelSettings.model_fields if key in kwargs}
        )
        super().__init__(*args, **kwargs)

    @staticmethod
    def name() -> str:
        return "fibona"

    def populate_context_post_run(self, context) -> None:
        """Sum usage over the model responses in Fibona's run logs (FIBONA_LOG_DIR, see run)."""
        totals = {"n_input_tokens": 0, "n_cache_tokens": 0, "n_output_tokens": 0, "cost_usd": 0.0}
        missing = set()
        seen = False
        for path in self.logs_dir.glob("*.jsonl"):
            for line in path.read_text().splitlines():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    self.logger.warning("Incomplete Fibona log record; usage totals are unknown")
                    missing.update(totals)
                    continue
                if record.get("event") == "model_error":  # Rounds before the error are not logged with usage.
                    missing.update(totals)
                if record.get("event") != "model_response":
                    continue
                usage = record["response"].get("usage") or {}
                seen = True
                values = {
                    "n_input_tokens": usage.get("input_tokens"),
                    "n_cache_tokens": (usage.get("input_tokens_details") or {}).get("cached_tokens"),
                    "n_output_tokens": usage.get("output_tokens"),
                    "cost_usd": usage.get("cost"),
                }
                for key, value in values.items():
                    if value is None:
                        missing.add(key)
                    else:
                        totals[key] += value
        if seen:
            for key, value in totals.items():
                setattr(context, key, None if key in missing else value)
        try:
            trajectory = convert(self.logs_dir, self.model_name)
        except Exception:
            self.logger.warning("Could not convert Fibona's run logs to a trajectory", exc_info=True)
            return
        if trajectory is not None:
            (self.logs_dir / "trajectory.json").write_text(format_trajectory_json(trajectory.to_json_dict()))

    async def install(self, environment) -> None:
        await self.exec_as_agent(
            environment,
            command="set -euo pipefail; UV_PYTHON_INSTALL_DIR=/opt/fibona/python /opt/fibona/dist/uv tool install "
            "--python 3.12 --offline --no-index --find-links /opt/fibona/dist /opt/fibona/dist/fibona_agent-*.whl; "
            f"printf %s {shlex.quote(BOOTSTRAP)} > $HOME/fibona-bootstrap.py",
        )

    @with_prompt_template
    async def run(self, instruction, environment, context) -> None:
        env = self.model_settings.environment(self.model_name or "gpt-6-astra", self.base_url)
        if env.get("FIBONA_REASONING_EFFORT", "medium") not in {"medium", "high", "xhigh", "max"}:
            raise ValueError(
                "Fibona supports reasoning_effort medium/high/xhigh/max; select an agent that supports the requested effort"
            )
        env["FIBONA_LOG_DIR"] = "/logs/agent"
        if (memory_mb := environment.task_env_config.memory_mb) is not None:
            instruction += (
                f"\n\nDeclared memory budget for this task's agent container: {memory_mb} MiB. "
                "Use this task budget even if system tools report more host memory."
            )
        if self.skills_dir:
            instruction += (
                f"\n\nTask skills are available in {self.skills_dir}. "
                "Read the SKILL.md files there and follow applicable instructions."
            )
        if self.mcp_servers:
            instruction += "\n\nConnect to the supplied MCP servers and call their tools from your Python cells:\n"
            instruction += json.dumps(
                [server.model_dump(mode="json", exclude_none=True) for server in self.mcp_servers]
            )
        await self.exec_as_agent(
            environment,
            command=f"printf %s {shlex.quote(instruction)} > /logs/agent/instruction.txt; "  # The trajectory's first step.
            'export PATH="$HOME/.local/bin:/opt/fibona/dist:$PATH"; '
            "$HOME/.local/bin/fibona --boot-mode $HOME/fibona-bootstrap.py "
            f"{shlex.quote(instruction)} < /dev/null > /logs/agent/fibona.txt 2>&1; "
            'status=$?; tail -c 65536 /logs/agent/fibona.txt; exit "$status"',
            env=env,
        )
