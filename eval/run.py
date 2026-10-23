"""Run a config: one `harbor run` that schedules every task on the config's hosts through SSHHosts.

Usage: python run.py <config.yaml> [hosts], with harbor installed (the Makefile's $(PYTHON)).
With `hosts`, prints the config's hosts one per line instead, for syncing them.
See configs/example.yaml for the schema.
"""

import json
import sys
from pathlib import Path

import yaml
from harbor.cli.main import app
from pydantic import BaseModel, ConfigDict, Field, PositiveInt

# Synced to every host by `make sync`. EC2-style environments bind host paths at the same container path.
MOUNTS = [{"type": "bind", "source": "/opt/fibona", "target": "/opt/fibona", "read_only": True}]


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run: str = Field(pattern=r"^[\w.-]+$")  # Job name: results go to jobs/<run>.
    dataset: str  # As `harbor run -d` takes it: name@version or org/name@ref.
    model: str  # As `harbor run -m` takes it, e.g. openai/gpt-6-astra.
    hosts: dict[str, PositiveInt] = Field(min_length=1)  # user@host -> tasks it runs at once.
    tasks: list[str] = []  # Task name globs; all tasks when empty.
    exclude: list[str] = []  # Task name globs to skip.
    limit: PositiveInt | None = None  # At most this many tasks, after filtering.
    extra_args: list[str] = []  # Passed to `harbor run` as is, e.g. ["--timeout-multiplier", "2"].


def harbor_args(config: RunConfig) -> list[str]:
    args = ["run", "-y", "-d", config.dataset, "-m", config.model, "-a", "harbor_agent:Fibona"]
    args += ["--env", "ssh_hosts:SSHHosts", "--ek", "hosts=" + ",".join(f"{h}:{n}" for h, n in config.hosts.items())]
    args += ["-n", str(sum(config.hosts.values())), "--mounts", json.dumps(MOUNTS), "--max-retries", "2"]
    args += ["--jobs-dir", "jobs", "--job-name", config.run]
    for task in config.tasks:
        args += ["-i", task]
    for task in config.exclude:
        args += ["-x", task]
    if config.limit is not None:
        args += ["-l", str(config.limit)]
    return args + config.extra_args


def main() -> int:
    config = RunConfig.model_validate(yaml.safe_load(Path(sys.argv[1]).read_text()))
    if sys.argv[2:] == ["hosts"]:
        print("\n".join(config.hosts))
        return 0
    app(harbor_args(config), prog_name="harbor")
    return 0


if __name__ == "__main__":
    sys.exit(main())
