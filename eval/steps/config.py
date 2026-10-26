"""Job configs (see example.yaml); once its workers exist, a job's groups are all fixed hosts and it yields Harbor's arguments."""

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from plugins.hosts import Resources, SSHSettings
from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, PositiveInt, model_validator
from steps.workers import ECSSpec

# Synced to every host by job.py. EC2-style environments bind host paths at the same container path.
MOUNTS = [{"type": "bind", "source": "/opt/fibona", "target": "/opt/fibona", "read_only": True}]


class ModelSettings(BaseModel):
    """call_me defaults (plugins/bootstrap.py) and API retries, passed to plugins.agent; unset ones keep Fibona's own."""

    model_config = ConfigDict(extra="forbid")

    reasoning_effort: Literal["medium", "high", "xhigh"] | None = (
        None  # When the agent doesn't pick one; Fibona: medium.
    )
    max_output_tokens: PositiveInt | None = None  # Per call, reasoning included, when the agent doesn't set one.
    api_retries: NonNegativeInt | None = None  # OpenAI client retries per call; the plugin's default is 8.


class JobGroup(BaseModel):
    """A task group on fixed `hosts:`, or on workers that `ecs:` launches for this job."""

    model_config = ConfigDict(extra="forbid")

    hosts: dict[str, Resources] = {}
    ecs: ECSSpec | None = None
    tasks: list[str] = []  # Task name globs, matched without the registry org prefix; empty matches all.

    @model_validator(mode="after")
    def one_source(self) -> "JobGroup":
        if bool(self.hosts) == bool(self.ecs):
            raise ValueError("Give a group either hosts or ecs.")
        return self


class JobConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[\w.-]+$")  # The run is <UTC time>-<name>.
    archive: str = Field(pattern=r"^tos://")  # tos://bucket/prefix; the job lands in <archive>/<run>/.
    dataset: str  # As `harbor run -d` takes it: name@version or org/name@ref.
    model: str  # As `harbor run -m` takes it, e.g. openai/gpt-6-astra.
    model_settings: ModelSettings = ModelSettings()
    settings: SSHSettings  # Shared SSH login, private key path and port for sync and Harbor.
    groups: list[JobGroup] = Field(min_length=1)  # First matching group supplies the task's hosts.
    exclude: list[str] = []  # Task name globs to skip.
    limit: PositiveInt | None = None  # At most this many tasks, after filtering.
    extra_args: list[str] = []  # Passed to `harbor run` as is, e.g. ["--timeout-multiplier", "2"].

    def run_name(self) -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d-%H%M%S-") + self.name

    # The rest works from a job whose groups are all on fixed hosts, i.e. after job.py launched its workers.
    @property
    def hosts(self) -> dict[str, Resources]:
        hosts: dict[str, Resources] = {}
        for group in self.groups:
            for host, capacity in group.hosts.items():
                if host in hosts and hosts[host] != capacity:
                    raise ValueError(f"Conflicting resource budgets for {host}.")
                hosts[host] = capacity
        return hosts

    def ssh(self, host: str) -> list[str]:
        return ssh_command(self.settings, host)

    def harbor_args(self, job_dir: Path) -> list[str]:
        """`harbor run` arguments: every task scheduled on these hosts through plugins.hosts."""
        args = ["run", "-y", "-d", self.dataset, "-m", self.model, "-a", "plugins.agent:Fibona"]
        for key, value in self.model_settings.model_dump(exclude_none=True).items():
            args += ["--ak", f"{key}={value}"]
        args += ["--env", "plugins.hosts:SSHHosts"]
        args += ["--ek", "settings=" + json.dumps(self.settings.model_dump(mode="json"))]
        args += [
            "--plugin",
            "plugins.hosts:HostPool",
            "--plugin-kwarg",
            "groups=" + json.dumps([g.model_dump(exclude={"ecs"}) for g in self.groups]),
        ]
        # One CPU per trial is the broad upper bound; HostPool applies each task's actual resource demand.
        args += [
            "-n",
            str(sum(host.cpus for host in self.hosts.values())),
            "--mounts",
            json.dumps(MOUNTS),
            "--max-retries",
            "2",
        ]
        args += ["--jobs-dir", str(job_dir / "jobs"), "--job-name", job_dir.name]
        # Harbor package filters match org/task, while group patterns use the short task name.
        prefix = "*/" if "/" in self.dataset.split("@", 1)[0] else ""
        if all(group.tasks for group in self.groups):
            for task in dict.fromkeys(task for group in self.groups for task in group.tasks):
                args += ["-i", prefix + task]
        for task in self.exclude:
            args += ["-x", prefix + task]
        if self.limit is not None:
            args += ["-l", str(self.limit)]
        return args + self.extra_args


def ssh_command(settings: SSHSettings, host: str) -> list[str]:
    pk = settings.pk.expanduser()
    if not pk.is_file() or not os.access(pk, os.R_OK):
        sys.exit(f"Cannot read SSH key {pk}; run as a user with access to this key.")
    return [
        "ssh",
        "-p",
        str(settings.ports.get(host, settings.port)),
        "-l",
        settings.user,
        "-i",
        str(pk),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
