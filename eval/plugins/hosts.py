"""Harbor environment that runs each trial's Docker Compose project on one of our own hosts over SSH.

Harbor's EC2 environment already does everything over SSH once it has a host: staging, docker compose, exec,
file transfer and cleanup. This subclass attaches to a fixed host pool instead of AWS, so one harbor process
schedules every trial and writes one job.

Pass settings={"user": "me", "pk": "/path/to/key", "port": 22} and
groups=[{"hosts": {"node1": {"cpus": 56, "memory_mb": 196608}}, "tasks": ["*"]}]
through --plugin-kwarg (JSON) to plugins.hosts:HostPool; JobConfig.harbor_args in steps/config.py does this for a job.
Groups match task names in order; the first match wins. Empty tasks matches all tasks.
Each host needs Docker with Compose, passwordless SSH from the head and passwordless sudo.
"""

import asyncio
import io
import json
import shlex
import shutil
import tarfile
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory, TemporaryFile
from typing import Annotated, ClassVar

import yaml
from harbor.environments import ec2
from harbor.environments.capabilities import EnvironmentCapabilities
from harbor.environments.docker import RESOURCES_COMPOSE_NAME
from harbor.environments.tar_transfer import extract_dir
from harbor.models.task.config import TaskConfig as TaskDefinition
from harbor.models.task.verifier_mode import resolve_effective_verifier_env_config
from harbor.trial.hooks import TrialEvent
from pydantic import BaseModel, ConfigDict, Field, PositiveInt
from steps.retry import network_retry, transient_output


def proxy_bypass(compose: dict, inherited: list[str], image_env: dict[str, list[str]]) -> dict:
    """Augment bypass entries without changing external proxy routing or task source."""
    services = compose.get("services", {})
    names = set(services)
    for service in services.values():
        for network in (service.get("networks") or {}).values():
            if network:
                names.update(network.get("aliases") or [])
    override = {"services": {}}
    for name, service in services.items():
        env = service.get("environment") or {}
        image = dict(item.split("=", 1) for item in image_env.get(service.get("image"), []) if "=" in item)
        values = [
            *inherited,
            *(image.get(k, "") for k in ("NO_PROXY", "no_proxy")),
            *(env.get(k) or "" for k in ("NO_PROXY", "no_proxy")),
        ]
        entries = {entry.strip() for value in values for entry in value.split(",") if entry.strip()}
        bypass = ",".join(sorted(entries | names | {"localhost", "127.0.0.1", "::1"}))
        override["services"][name] = {"environment": {"NO_PROXY": bypass, "no_proxy": bypass}}
    return override


class SSHSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user: str = Field(min_length=1)
    pk: Path
    port: int = Field(default=22, ge=1, le=65535)
    ports: dict[str, Annotated[int, Field(ge=1, le=65535)]] = {}  # Per-host overrides of port.
    registry: str | None = None  # Optional registry/namespace holding byte-identical task images.


def mirror_image(image: str, registry: str | None) -> str:
    if not registry or image.startswith(registry.rstrip("/") + "/") or "${" in image:
        return image
    repository, separator, digest = image.partition("@")
    return registry.rstrip("/") + "/" + repository.rsplit("/", 1)[-1] + (separator + digest if separator else "")


class Resources(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cpus: PositiveInt
    memory_mb: PositiveInt
    gpus: int = Field(default=0, ge=0)

    def fits(self, *requests: "Resources") -> bool:
        return all(
            sum(getattr(request, key) for request in requests) <= getattr(self, key)
            for key in ("cpus", "memory_mb", "gpus")
        )


class TaskGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hosts: dict[str, Resources] = Field(min_length=1)
    tasks: list[str] = []

    def matches(self, task: str) -> bool:
        return any(fnmatchcase(task.rsplit("/", 1)[-1], pattern) for pattern in self.tasks or ["*"])


class SSHComposeOps(ec2._EC2ComposeOps):
    """Transfer artifacts through tar; download archives to disk instead of buffering them in RAM."""

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
        *,
        service: str | None = None,
    ) -> ec2.ExecResult:
        # Harbor's nested login shells overwrite explicit exec PATH overrides.
        # Honor them (including --verifier-env); leave default commands unchanged.
        if service in (None, "main") and env is not None and "PATH" in env:
            command = f"export PATH={shlex.quote(env['PATH'])}; {command}"
        return await super().exec(command, cwd=cwd, env=env, timeout_sec=timeout_sec, user=user, service=service)

    async def _transfer(self, args: list[str], stdin: bytes | None = None, *, output=None) -> bytes:
        env = self._env._compose_env_vars()
        command = shlex.join(["env", *(f"{key}={value}" for key, value in env.items())])
        command += " " + self._env._compose_cmd(["exec", "-T", "-u", "0", *args], env_vars_for_sudo=env)
        if output is None:
            stdout, stderr, code = await self._env._ssh_raw(command, stdin=stdin, timeout_sec=300)
        else:
            process = await asyncio.create_subprocess_exec(
                *self._env._ssh_base_cmd(stdin_null=True),
                command,
                stdout=output,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr, code = await self._env._collect_process(process, timeout_sec=300)
        if code:
            raise RuntimeError(f"Container tar transfer failed: {stderr.decode(errors='replace')}")
        return stdout

    async def upload_file(self, source_path, target_path) -> None:
        target = PurePosixPath(target_path)
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as archive:
            archive.add(source_path, arcname=target.name)
        await self._transfer(
            [
                "main",
                "sh",
                "-c",
                f"mkdir -p {shlex.quote(str(target.parent))} && tar xf - -C {shlex.quote(str(target.parent))}",
            ],
            data.getvalue(),
        )

    async def upload_dir(self, source_dir, target_dir) -> None:
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w") as archive:
            # Keep the target directory's ownership/mode, especially writable log bind mounts.
            for child in Path(source_dir).iterdir():
                archive.add(child, arcname=child.name)
        await self._transfer(
            ["main", "sh", "-c", f"mkdir -p {shlex.quote(target_dir)} && tar xf - -C {shlex.quote(target_dir)}"],
            data.getvalue(),
        )

    async def download_file(self, source_path, target_path, *, service=None) -> None:
        source, target = PurePosixPath(source_path), Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryFile(dir=target.parent) as data:
            await self._transfer(
                [service or "main", "tar", "cf", "-", "-C", str(source.parent), source.name], output=data
            )
            data.seek(0)
            with tarfile.open(fileobj=data, mode="r") as archive:
                member = archive.getmembers()[0]
                member.name = target.name
                archive.extract(member, target.parent, filter="data")

    async def download_dir(self, source_dir, target_dir, *, service=None) -> None:
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        with TemporaryFile(dir=target.parent) as data:
            await self._transfer([service or "main", "tar", "cf", "-", "-C", source_dir, "."], output=data)
            data.seek(0)
            extract_dir(data, target)


# Attach mode with the overrides below never calls AWS. boto3, which EC2Environment checks for, already
# comes with harbor through litellm.
class SSHHosts(ec2.EC2Environment):
    # The job plugin reserves resources and exclusive GPUs through separate verification.
    assigned: ClassVar[dict[str, tuple[str, tuple[int, ...]]]] = {}

    @property
    def capabilities(self) -> EnvironmentCapabilities:
        return super().capabilities.model_copy(update={"gpus": True})

    @classmethod
    def preflight(cls) -> None:
        if not shutil.which("ssh"):
            raise SystemExit("SSHHosts requires the OpenSSH client (`ssh`) on PATH.")

    def __init__(self, *args, settings: dict, **kwargs) -> None:
        settings = SSHSettings.model_validate(settings)
        super().__init__(
            *args,
            region="none",
            launch_mode="attach",
            instance_id="ssh-hosts",
            bootstrap_docker=False,
            ssh_user=settings.user,
            ssh_key_path=settings.pk,
            ssh_port=settings.port,
            **kwargs,
        )
        self._registry = settings.registry
        self._ssh_ports = settings.ports
        self._compose_ops = SSHComposeOps(self)
        if self.task_env_config.docker_image:
            self.task_env_config = self.task_env_config.model_copy(
                update={"docker_image": mirror_image(self.task_env_config.docker_image, self._registry)}
            )

    async def _wait_for_instance_ready(self) -> None:
        pass

    def _compose_file_flags(self) -> list[str]:
        flags = super()._compose_file_flags()
        if path := getattr(self, "_proxy_override", None):
            flags += ["-f", path]
        return flags

    async def _prepare_proxy_bypass(self) -> None:
        rendered = await super()._compose_exec(["config", "--format", "json"], timeout_sec=30)
        if rendered.return_code != 0:
            raise RuntimeError("Cannot resolve task Compose configuration")
        compose = json.loads(rendered.stdout)
        # Docker client defaults are otherwise injected after Compose resolution.
        script = (
            "import json,os,pathlib; "
            "p=pathlib.Path(os.environ.get('DOCKER_CONFIG',str(pathlib.Path.home()/'.docker')))/'config.json'; "
            "d=json.loads(p.read_text()) if p.exists() else {}; "
            "proxies=d.get('proxies',{}); "
            "v=proxies.get(os.environ.get('DOCKER_HOST','unix:///var/run/docker.sock'),proxies.get('default',{})); "
            "print(json.dumps([v.get('noProxy','')]))"
        )
        command = ("sudo -n " if self._docker_cmd == "sudo docker" else "") + "python3 -c " + shlex.quote(script)
        defaults = await self._ssh_exec(command, cwd="/", timeout_sec=30)
        if defaults.return_code != 0:
            raise RuntimeError("Cannot read worker proxy bypass defaults")
        inherited = json.loads(defaults.stdout)
        # Compose v2 names build-only images <project>-<service>.
        for name, service in compose["services"].items():
            if service.get("build") and not service.get("image"):
                service["image"] = f"{compose['name']}-{name}"
        images = list(dict.fromkeys(s["image"] for s in compose["services"].values() if s.get("image")))
        image_env = {}
        if images:
            result = await self._ssh_exec(
                shlex.join([*self._docker_parts(), "image", "inspect", "--format", "{{json .Config.Env}}", *images]),
                cwd="/",
                timeout_sec=30,
            )
            if result.return_code != 0:
                raise RuntimeError("Cannot read prepared image proxy defaults")
            image_env = dict(
                zip(images, (json.loads(line) or [] for line in result.stdout.split("\n") if line), strict=True)
            )
        override = proxy_bypass(compose, inherited, image_env)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "docker-compose-proxy.json"
            path.write_text(json.dumps(override))
            remote = f"{self._compose_dir_vm}/{path.name}"
            await self._upload_file_to_host(path, remote)
        self._proxy_override = remote

    async def _compose_exec(self, subcommand: list[str], timeout_sec: int | None = None) -> ec2.ExecResult:
        if subcommand[:1] != ["up"]:
            return await super()._compose_exec(subcommand, timeout_sec=timeout_sec)

        @network_retry
        async def prepare():
            pulled = await super(SSHHosts, self)._compose_exec(
                ["pull", "--ignore-buildable", "--policy", "missing"],
                timeout_sec=round(self.task_env_config.build_timeout_sec),
            )
            if pulled.return_code != 0:
                if transient_output((pulled.stderr or "") + (pulled.stdout or "")):
                    raise ConnectionError("Transient image preparation failure")
                raise RuntimeError(f"Image preparation failed: {pulled.stdout} {pulled.stderr}")

        await prepare()
        await self._prepare_proxy_bypass()
        return await self._start_with_log([*subcommand, "--pull", "never"], timeout_sec)

    async def _start_with_log(self, subcommand, timeout_sec):
        # Keep output on the worker even when SSH's timeout would discard its buffers.
        directory = self.trial_paths.trial_dir / "startup" / self._compose_project_name
        directory.mkdir(parents=True, exist_ok=True)
        remote = f"{self._session_dir_vm}/startup.log"
        env = self._compose_env_vars()
        command = self._compose_cmd(subcommand, env_vars_for_sudo=env if self._docker_cmd == "sudo docker" else None)
        if timeout_sec is not None:
            command = f"timeout --signal=TERM --kill-after=10s {timeout_sec}s {command}"
        command += f" > {shlex.quote(remote)} 2>&1"
        result = None
        try:
            result = await self._ssh_exec(
                command,
                cwd="/",
                env=None if self._docker_cmd == "sudo docker" else env,
                timeout_sec=None if timeout_sec is None else timeout_sec + 30,
            )
        finally:
            await self._capture_startup(remote, directory, result)
        if result.return_code != 0 and (directory / "compose.log").exists():
            result.stderr = (directory / "compose.log").read_text()[-8192:]
        if result.return_code == 124:
            raise TimeoutError(f"Compose startup exceeded {timeout_sec}s; see {directory}")
        return result

    async def _capture_startup(self, remote, directory, result):
        try:
            async with asyncio.timeout(30):
                if result is None or result.return_code != 0:
                    (directory / "transport.json").write_text(
                        json.dumps(
                            {
                                "return_code": result.return_code if result else None,
                                "stderr": result.stderr if result else "startup interrupted",
                            }
                        )
                    )
                await self._download_file_from_host(remote, directory / "compose.log")
                if result is None or result.return_code != 0:
                    snapshot = await super()._compose_exec(["ps", "--all", "--format", "json"], timeout_sec=20)
                    (directory / "containers.json").write_text(snapshot.stdout or "")
                    ids = await super()._compose_exec(["ps", "--all", "--quiet"], timeout_sec=20)
                    containers = [c for c in (ids.stdout or "").split() if all(x in "0123456789abcdef" for x in c)]
                    if containers:
                        states = await self._ssh_exec(
                            shlex.join(
                                [
                                    *self._docker_parts(),
                                    "inspect",
                                    "--format",
                                    '{"id":{{json .Id}},"state":{{json .State}}}',
                                    *containers,
                                ]
                            ),
                            cwd="/",
                            timeout_sec=10,
                        )
                        (directory / "states.jsonl").write_text((states.stdout or "") + (states.stderr or ""))
                    logs = await super()._compose_exec(["logs", "--tail", "100", "--timestamps"], timeout_sec=10)
                    (directory / "containers.log").write_text((logs.stdout or "") + (logs.stderr or ""))
        except Exception as error:
            self.logger.warning("Startup diagnostic collection failed: %s", type(error).__name__)

    async def _resolve_host(self) -> None:
        self._host, self._gpu_ids = self.assigned[self.trial_paths.trial_dir.name]
        self.ssh_port = self._ssh_ports.get(self._host, self.ssh_port)
        self.logger.info("SSH host %s, GPUs %s", self._host, self._gpu_ids)

    def _resolve_volumes(self) -> list[dict]:
        volumes = super()._resolve_volumes()
        for volume in volumes:
            if volume["type"] == "bind" and volume["target"].startswith("/logs/"):
                # EC2 normally dedicates a VM to one trial. Our shared hosts need isolated log mounts.
                volume["source"] = self._session_dir_vm + "/mounts" + volume["target"]
        return volumes

    async def _stage_mounts_compose_file(self, volumes) -> None:
        from harbor.environments.docker import write_mounts_compose_file

        with TemporaryDirectory() as directory:
            path = Path(directory) / "docker-compose-mounts.json"
            write_mounts_compose_file(path, volumes)
            compose = json.loads(path.read_text())
            for source in [self._environment_docker_compose_path, *self.extra_docker_compose_paths]:
                if not source.exists():
                    continue
                # Read image strings only; BaseLoader also accepts Compose's !reset/!override tags.
                for name, service in (
                    (yaml.load(source.read_text(), Loader=yaml.BaseLoader) or {}).get("services", {}).items()
                ):
                    if isinstance(service, dict) and service.get("image"):
                        compose["services"].setdefault(name, {})["image"] = mirror_image(
                            service["image"], self._registry
                        )
            path.write_text(json.dumps(compose))
            await self._upload_file_to_host(path, f"{self._compose_dir_vm}/{path.name}")

    async def _stage_resources_compose_file(self) -> None:
        await super()._stage_resources_compose_file()
        if not self._effective_gpus:
            return
        with TemporaryDirectory() as directory:
            path = Path(directory) / RESOURCES_COMPOSE_NAME
            # Fetch the CPU/memory overlay before adding an exclusive device reservation.
            await self._download_file_from_host(f"{self._compose_dir_vm}/{path.name}", path)
            compose = json.loads(path.read_text())
            main = compose["services"]["main"]
            resources = main.setdefault("deploy", {}).setdefault("resources", {})
            resources.setdefault("reservations", {})["devices"] = [
                {
                    "driver": "nvidia",
                    "device_ids": [str(i) for i in self._gpu_ids[: self._effective_gpus]],
                    "capabilities": ["gpu"],
                }
            ]
            path.write_text(json.dumps(compose))
            await self._upload_file_to_host(path, f"{self._compose_dir_vm}/{path.name}")


class HostPool:
    """Reserve before environment build timeouts start; keep the reservation through verification."""

    def __init__(self, groups: list[dict]) -> None:
        self.groups = [TaskGroup.model_validate(group) for group in groups]
        self.condition = asyncio.Condition()
        self.reserved: dict[str, Resources] = {}
        self.requests = {}

    async def on_job_start(self, job) -> None:
        # Harbor has already cached the pinned task definitions. Reuse its resolved resources,
        # including verifier inheritance, rather than maintaining a second TB4 resource table.
        for task_id, download in job._task_download_results.items():
            task = TaskDefinition.model_validate_toml((download.path / "task.toml").read_text())
            phases = [task.environment]
            for step in task.steps or [None]:
                verifier = resolve_effective_verifier_env_config(task, step)
                if verifier is not None:
                    phases.append(verifier)
            request = Resources(
                cpus=max(phase.cpus or 1 for phase in phases),
                memory_mb=max(phase.memory_mb or 2048 for phase in phases),
                gpus=max(phase.gpus or 0 for phase in phases),
            )
            group = next(group for group in self.groups if group.matches(task_id.get_name()))
            if not any(capacity.fits(request) for capacity in group.hosts.values()):
                raise ValueError(f"No host can fit {task_id.get_name()}: {request.model_dump()}")
            self.requests[task_id] = request
        job.add_hook(TrialEvent.START, self.acquire)
        job.add_hook(TrialEvent.END, self.release)

    async def on_job_end(self, job_result) -> None:
        pass

    async def acquire(self, event) -> None:
        group = next(group for group in self.groups if group.matches(event.task_name))
        request = self.requests[event.config.task.get_task_id()]
        async with self.condition:
            while True:
                used = {
                    host: [
                        resources for trial, resources in self.reserved.items() if SSHHosts.assigned[trial][0] == host
                    ]
                    for host in group.hosts
                }
                free = [host for host, capacity in group.hosts.items() if capacity.fits(request, *used[host])]
                if free:
                    host = min(
                        free,
                        key=lambda h: max(
                            sum(r.cpus for r in used[h]) / group.hosts[h].cpus,
                            sum(r.memory_mb for r in used[h]) / group.hosts[h].memory_mb,
                        ),
                    )
                    occupied = {gpu for h, gpu_ids in SSHHosts.assigned.values() if h == host for gpu in gpu_ids}
                    gpu_ids = tuple(i for i in range(group.hosts[host].gpus) if i not in occupied)[: request.gpus]
                    SSHHosts.assigned[event.trial_name] = host, gpu_ids
                    self.reserved[event.trial_name] = request
                    return
                await self.condition.wait()

    async def release(self, event) -> None:
        async with self.condition:
            SSHHosts.assigned.pop(event.trial_name, None)
            self.reserved.pop(event.trial_name, None)
            self.condition.notify_all()
