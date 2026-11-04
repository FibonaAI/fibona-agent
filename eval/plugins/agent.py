"""Harbor installed agent: runs Fibona with bootstrap.py once per task inside the task container.

Uses the local Harbor bootstrap and passes reasoning effort through the Fibona CLI.

Installation uploads the current local source and installs uv, Python and locked dependencies in the task container.
Tasks with MCP servers additionally install the MCP SDK.
After the run, Fibona's run logs become agent/trajectory.json (ATIF), which `harbor view` shows; see plugins/utils.py.
"""

import asyncio
import json
import shlex
import tarfile
import tempfile
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
    _REPO = '"$HOME/.local/share/harbor-fibona"'

    def __init__(self, *args, fast: bool = False, ultrafast: bool = False, **kwargs) -> None:
        if fast and ultrafast:
            raise ValueError("fast and ultrafast are mutually exclusive")
        self.fast, self.ultrafast = fast, ultrafast
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
        incomplete = False
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
                incomplete = incomplete or bool(usage.get("incomplete"))
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
        context.metadata = {**(context.metadata or {}), "incomplete": incomplete or bool(missing) or not seen}
        try:
            trajectory = convert(self.logs_dir, self.model_name)
        except Exception:
            self.logger.warning("Could not convert Fibona's run logs to a trajectory", exc_info=True)
            return
        if trajectory is not None:
            (self.logs_dir / "trajectory.json").write_text(format_trajectory_json(trajectory.to_json_dict()))

    async def install(self, environment) -> None:
        source = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix="harbor-fibona-") as directory:
            archive_path = Path(directory) / "source.tar.gz"

            def pack_source():
                def source_file(member):
                    if "__pycache__" in Path(member.name).parts or member.name.endswith(".pyc"):
                        return None
                    return member

                with tarfile.open(archive_path, "w:gz") as archive:
                    for name in (
                        "fibona.py",
                        "core",
                        "agent_assets",
                        "bootstraps",
                        "pyproject.toml",
                        "uv.lock",
                        "README.md",
                        "LICENSE",
                    ):
                        archive.add(source / name, arcname=name, filter=source_file)

            await asyncio.to_thread(pack_source)
            await self.ensure_system_dependencies(environment, ("curl", "ca_certificates"))
            await environment.upload_file(archive_path, "/installed-agent/fibona-source.tar.gz")
        await self.exec_as_agent(
            environment,
            command="set -euo pipefail; "
            'curl -fsSL https://astral.sh/uv/0.13.5/install.sh | env UV_INSTALL_DIR="$HOME/.local/bin" sh; '
            f"mkdir -p {self._REPO}; "
            f"tar -xzf /installed-agent/fibona-source.tar.gz -C {self._REPO}; "
            f'"$HOME/.local/bin/uv" sync --project {self._REPO} --python 3.12 --frozen --no-dev; '
            f"printf %s {shlex.quote(BOOTSTRAP)} > $HOME/fibona-bootstrap.py",
        )
        if self.mcp_servers:
            await self.exec_as_agent(
                environment,
                command=f'"$HOME/.local/bin/uv" pip install --python {self._REPO}/.venv/bin/python "mcp==1.25.0"',
            )

    @with_prompt_template
    async def run(self, instruction, environment, context) -> None:
        env = self.model_settings.environment(self._parsed_model_name or "gpt-6-astra")
        flags = []
        if effort := self.model_settings.reasoning_effort:
            flags.extend(["--reasoning-effort", effort])
        if lowest_effort := self.model_settings.lowest_reasoning_effort:
            flags.extend(["--lowest-reasoning-effort", lowest_effort])
        if self.fast:
            flags.append("--fast")
        if self.ultrafast:
            flags.append("--ultrafast")
        env["FIBONA_LOG_DIR"] = "/logs/agent"
        if (memory_mb := environment.task_env_config.memory_mb) is not None:
            instruction += (
                f"\n\nDeclared memory budget for this task's agent container: {memory_mb} MiB. "
                "Use this task budget even if system tools report more host memory."
            )
        if self.skills_dir:
            env["FIBONA_SKILLS_DIR"] = self.skills_dir
        if self.mcp_servers:
            config = {
                server.name: {**server.model_dump(exclude={"headers"}), "header_env": {}} for server in self.mcp_servers
            }
            env["FIBONA_MCP_CONFIG"] = "/logs/agent/fibona-mcp.json"
            await self.exec_as_agent(
                environment,
                command=f"printf %s {shlex.quote(json.dumps(config))} > {env['FIBONA_MCP_CONFIG']}",
            )
        await self.exec_as_agent(
            environment,
            command=f"printf %s {shlex.quote(instruction)} > /logs/agent/instruction.txt; "  # The trajectory's first step.
            'export PATH="$HOME/.local/bin:$PATH"; '
            f"{self._REPO}/.venv/bin/python {self._REPO}/fibona.py --boot-mode $HOME/fibona-bootstrap.py "
            f"{shlex.join(flags)} {shlex.quote(instruction)} < /dev/null > /logs/agent/fibona.txt 2>&1; "
            'status=$?; tail -c 65536 /logs/agent/fibona.txt; exit "$status"',
            env=env,
        )
