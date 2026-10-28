"""Run the checkout's experimental Python agent through the standard eval job lifecycle."""

import json
from datetime import datetime
from pathlib import Path

from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from steps.config import ModelSettings

SOURCE = Path(__file__).resolve().parents[2] / "experiments/new_core"
ENTRY = "await run_agent(interactive=True)  # noqa: F704"


def batch_bootstrap(source: str) -> str:
    """Keep cell 0 intact, replacing only the interactive entry with the eval entry."""
    if source.count(ENTRY) != 1 or not source.rstrip().endswith(ENTRY):
        raise ValueError("new_core bootstrap entry changed; review the eval adapter")
    entry = 'await run_agent(Path("/installed-agent/new-core-task.txt").read_text(), interactive=False)\n'
    return source[: source.index(ENTRY)] + entry


def session_metrics(path: Path) -> dict:
    """Derive usage and logical-request latency from native records, without rewriting history."""
    totals = {"n_input_tokens": 0, "n_cache_tokens": 0, "n_output_tokens": 0, "cost_usd": 0.0}
    missing = set()
    requests, outcomes = {}, set()
    errors, anomalies, latencies = 0, 0, []
    complete = path.is_file()
    if complete:
        with path.open("rb") as source:
            index = 0
            for line in source:
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        raise ValueError("Expected a record object")
                except ValueError:
                    complete = False
                    continue
                if record.get("id") != index:
                    complete = False
                index += 1
                kind = record.get("type")
                if kind == "model_request":
                    requests[record["id"]] = record.get("time")
                elif kind in ("model_response", "model_request_error"):
                    request_id = record.get("request_id")
                    if request_id not in requests or request_id in outcomes:
                        complete = False
                        continue
                    outcomes.add(request_id)
                    try:
                        latency = (
                            datetime.fromisoformat(record["time"]) - datetime.fromisoformat(requests[request_id])
                        ).total_seconds()
                        latencies.append({"request_id": request_id, "seconds": latency, "status": record.get("status")})
                    except (KeyError, TypeError, ValueError):
                        complete = False
                    if kind == "model_request_error":
                        errors += 1
                        missing.update(totals)
                        continue
                    usage = record.get("usage") or {}
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
                    reasoning = (usage.get("output_tokens_details") or {}).get("reasoning_tokens")
                    if (
                        reasoning is not None
                        and usage.get("output_tokens") is not None
                        and reasoning > usage["output_tokens"]
                    ):
                        anomalies += 1
    pending = len(requests.keys() - outcomes)
    complete = complete and not pending
    return {
        "totals": {
            key: value if complete and requests and key not in missing else None for key, value in totals.items()
        },
        "known_usage_subtotal": totals,
        "missing_usage_fields": sorted(missing),
        "request_log_complete": complete,
        "model_calls": len(requests),
        "model_errors": errors,
        "unfinished_requests": pending,
        "token_anomalies": anomalies,
        "request_latencies": latencies,
    }


class NewCore(BaseInstalledAgent):
    capabilities = AgentCapabilities(skills=True, mcp_servers=True)

    def __init__(self, *args, base_url: str | None = None, **kwargs) -> None:
        self.base_url = base_url
        self.model_settings = ModelSettings.model_validate(
            {key: kwargs.pop(key) for key in ModelSettings.model_fields if key in kwargs}
        )
        super().__init__(*args, **kwargs)

    def task_instruction(self, instruction, environment) -> str:
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
        return instruction

    @staticmethod
    def name() -> str:
        return "fibona-new-core"

    async def install(self, environment) -> None:
        await environment.upload_dir(str(SOURCE), "/installed-agent/new-core")
        bootstrap = self.logs_dir / "bootstrap.py"
        bootstrap.write_text(batch_bootstrap((SOURCE / "bootstraps/python/main.py").read_text()))
        await environment.upload_file(str(bootstrap), "/installed-agent/new-core-batch.py")
        await self.exec_as_agent(
            environment,
            command="set -eu; export UV_PYTHON_INSTALL_DIR=/opt/fibona/python; "
            '/opt/fibona/dist/uv venv --python 3.12 --offline "$HOME/.local/share/fibona-new-core"; '
            "/opt/fibona/dist/uv pip install --offline --no-index --find-links /opt/fibona/dist "
            '--python "$HOME/.local/share/fibona-new-core/bin/python" /opt/fibona/dist/fibona_agent-*.whl',
        )

    @with_prompt_template
    async def run(self, instruction, environment, context) -> None:
        instruction = self.task_instruction(instruction, environment)
        task = self.logs_dir / "instruction.txt"
        task.write_text(instruction)
        await environment.upload_file(str(task), "/installed-agent/new-core-task.txt")
        env = self.model_settings.environment(self.model_name or "gpt-6-astra", self.base_url)
        env["FIBONA_SESSION"] = "/logs/agent/session.jsonl"
        await self.exec_as_agent(
            environment,
            command='"$HOME/.local/share/fibona-new-core/bin/python" /installed-agent/new-core/main.py '
            "--bootstrap /installed-agent/new-core-batch.py < /dev/null > /logs/agent/console.txt 2>&1; "
            'status=$?; tail -c 65536 /logs/agent/console.txt; exit "$status"',
            env=env,
        )

    def populate_context_post_run(self, context) -> None:
        metrics = session_metrics(self.logs_dir / "session.jsonl")
        for key, value in metrics["totals"].items():
            setattr(context, key, value)
        context.metadata = {key: value for key, value in metrics.items() if key != "request_latencies"}
        (self.logs_dir / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
