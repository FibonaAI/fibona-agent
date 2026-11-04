"""Job configs (see *.example.yaml): `local` runs on this machine alone, `groups` on a cluster. Once its workers exist,
a cluster job's groups are all fixed hosts and it yields Harbor's arguments."""

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from plugins.hosts import Resources, SSHSettings
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveInt,
    model_validator,
)
from steps.workers import ECSSpec


class Price(BaseModel):
    """USD per million tokens, for model calls whose API reports no usage.cost (Fibona's FIBONA_PRICE)."""

    model_config = ConfigDict(extra="forbid")

    input: NonNegativeFloat
    cached_input: NonNegativeFloat
    cache_write: NonNegativeFloat
    output: NonNegativeFloat


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = None
    max_output_tokens: PositiveInt | None = None
    api_retries: NonNegativeInt | None = None
    context_tokens: PositiveInt | None = None
    extra_body: dict[str, JsonValue] | None = None
    price: Price | None = None

    @model_validator(mode="after")
    def output_fits_context(self) -> "ModelSettings":
        if self.context_tokens is not None and self.max_output_tokens is not None:
            if self.max_output_tokens >= self.context_tokens:
                raise ValueError("max_output_tokens must be smaller than context_tokens")
        return self

    def environment(self, model: str, base_url: str | None = None) -> dict[str, str]:
        """Explicit YAML values override optional environment defaults; credentials remain private."""
        keys = ("FIBONA_API_KEY", "FIBONA_BASE_URL", *(f"FIBONA_{key.upper()}" for key in type(self).model_fields))
        env = {key: os.environ[key] for key in keys if os.environ.get(key)}
        env.setdefault("FIBONA_API_RETRIES", "8")
        for key, value in self.model_dump(exclude_none=True).items():
            env[f"FIBONA_{key.upper()}"] = json.dumps(value) if isinstance(value, dict) else str(value)
        if base_url is not None:
            env["FIBONA_BASE_URL"] = base_url
        env["FIBONA_MODEL"] = model
        return env


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
    base_url: str | None = Field(default=None, min_length=1)
    agent: str = Field(default="plugins.agent:Fibona", min_length=1)  # Harbor name or module:Class.
    model_settings: ModelSettings = ModelSettings()
    # Exactly one of local and groups. local: this machine alone, in its Docker as the current user, no SSH.
    local: Resources | None = None  # Scheduling budget on this machine.
    settings: SSHSettings | None = None  # groups only: shared SSH login, private key path and port.
    groups: list[JobGroup] = []  # A cluster; first matching group supplies the task's hosts.
    # Task images are mirrored here before the run and pulled from here, local or cluster; every machine's Docker
    # must be logged in to it. Without it, tasks pull from upstream.
    registry: str | None = None
    exclude: list[str] = []  # Task name globs to skip.
    limit: PositiveInt | None = None  # At most this many tasks, after filtering.
    extra_args: list[str] = []  # Passed to `harbor run` as is, e.g. ["--timeout-multiplier", "2"].

    @model_validator(mode="after")
    def one_mode(self) -> "JobConfig":
        if bool(self.local) == bool(self.groups):
            raise ValueError("Give either local (this machine alone) or groups (a cluster).")
        if self.local and self.settings:
            raise ValueError("local runs without SSH; drop settings.")
        if self.groups and not self.settings:
            raise ValueError("groups need settings.")
        return self

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

    def harbor_args(self, job_dir: Path, mount_source: str) -> list[str]:
        """`harbor run` arguments: every task scheduled through plugins.hosts, on these hosts or, for local, in this
        machine's Docker. mount_source is where job.py synced dist and python to; mounted at /opt/fibona."""
        args = ["run", "-y", "-d", self.dataset, "-m", self.model, "-a", self.agent]
        if self.base_url is not None:
            args += ["--ak", f"base_url={json.dumps(self.base_url)}"]
        for key, value in self.model_settings.model_dump(exclude_none=True).items():
            args += ["--ak", f"{key}={json.dumps(value)}"]
        if self.local:
            args += ["--env", "plugins.hosts:LocalDocker"]
            groups = [{"hosts": {"localhost": self.local.model_dump()}}]
        else:
            args += ["--env", "plugins.hosts:SSHHosts"]
            args += ["--ek", "settings=" + json.dumps(self.settings.model_dump(mode="json"))]
            groups = [g.model_dump(exclude={"ecs"}) for g in self.groups]
        if self.registry:
            args += ["--ek", "registry=" + json.dumps(self.registry)]
        args += ["--plugin", "plugins.hosts:HostPool", "--plugin-kwarg", "groups=" + json.dumps(groups)]
        mounts = [{"type": "bind", "source": mount_source, "target": "/opt/fibona", "read_only": True}]
        # One CPU per trial is the broad upper bound; HostPool applies each task's actual resource demand.
        cpus = self.local.cpus if self.local else sum(host.cpus for host in self.hosts.values())
        args += ["-n", str(cpus), "--mounts", json.dumps(mounts), "--max-retries", "2"]
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
