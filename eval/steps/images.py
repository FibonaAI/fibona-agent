"""Make sure our registry holds every runtime image a job's tasks use, byte for byte; idempotent.

An image already in the registry costs one manifest lookup; a missing one is copied from upstream by crane,
which keeps manifests as they are and so keeps pinned digests. Tag-only images follow their upstream tag.
The registry login is a short-lived CR token. Writes {image: {target, tasks, status}} to the job's images.json.
"""

import asyncio
import base64
import json
import os
import subprocess
import tempfile
import tomllib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from fnmatch import fnmatchcase
from pathlib import Path

import volcenginesdkcr
import yaml
from harbor.models.job.config import DatasetConfig
from harbor.tasks.client import TaskClient
from plugins.hosts import mirror_image
from steps.config import JobConfig
from steps.retry import network_retry, transient_output
from steps.workers import REGION

CRANE = str(Path(__file__).resolve().parent.parent / "bin" / "crane")


@network_retry
async def download(dataset: str) -> list[Path]:
    name, _, version = dataset.partition("@")
    config = DatasetConfig(name=name, **({"ref": version or "latest"} if "/" in name else {"version": version or None}))
    tasks = await config.get_task_configs()
    downloaded = await TaskClient().download_tasks(task_ids=[task.get_task_id() for task in tasks])
    return [result.path for result in downloaded.results]


def task_images(root: Path) -> tuple[str, list[str]]:
    """The task's short name and the images it runs as is: agent and verifier environments, Compose services."""
    task = tomllib.loads((root / "task.toml").read_text())
    images = [
        task.get("environment", {}).get("docker_image"),
        task.get("verifier", {}).get("environment", {}).get("docker_image"),
    ]
    for compose in [*root.glob("environment/**/docker-compose.yaml"), *root.glob("environment/**/docker-compose.yml")]:
        for service in ((yaml.safe_load(compose.read_text()) or {}).get("services") or {}).values():
            if isinstance(service, dict):
                images.append(service.get("image"))
    name = task.get("task", {}).get("name", root.parent.name).rsplit("/", 1)[-1]
    return name, [image for image in images if image and "${" not in image]


@network_retry
def crane(*args: str, env: dict) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run([CRANE, *args], env=env, capture_output=True, text=True, timeout=3600)
    except subprocess.TimeoutExpired as error:
        raise TimeoutError("Registry command timed out") from error
    if result.returncode and transient_output(result.stderr):
        raise ConnectionError(f"Transient registry connection failure: {result.stderr[-2000:]}")
    return result


def ensure(image: str, target: str, env: dict) -> str:
    """Return present or copied; raise when the copy fails or the digest differs."""
    repository, _, digest = target.partition("@")
    if not digest:
        upstream = crane("digest", image, env=env)
        if upstream.returncode:
            raise RuntimeError(upstream.stderr.strip())
        digest = upstream.stdout.strip()
    # Look up by digest without the tag: a moved or missing tag still counts if the bytes are there.
    name = repository.rsplit(":", 1)[0] if ":" in repository.rsplit("/", 1)[-1] else repository
    if crane("manifest", f"{name}@{digest}", env=env).returncode == 0:
        return "present"
    copied = crane("copy", image, repository, env=env)
    if copied.returncode:
        raise RuntimeError(copied.stderr.strip()[-2000:])
    if crane("digest", f"{name}@{digest}", env=env).stdout.strip() != digest:
        raise RuntimeError(f"{target} digest differs after copy")
    return "copied"


def ensure_images(config: JobConfig, out: Path) -> None:
    """Mirror the images of the tasks the job selects."""
    registry = config.settings.registry
    if not registry:
        print("No settings.registry; tasks pull images from upstream.")
        return
    patterns = [group.tasks for group in config.groups]
    include = None if not all(patterns) else [pattern for group in patterns for pattern in group]
    exclude = config.exclude
    images = defaultdict(list)
    for root in asyncio.run(download(config.dataset)):
        task, task_refs = task_images(root)
        if (include is None or any(fnmatchcase(task, p) for p in include)) and not any(
            fnmatchcase(task, p) for p in exclude
        ):
            for image in task_refs:
                images[image].append(task)

    host = registry.split("/", 1)[0]
    token = network_retry(volcenginesdkcr.CRApi().get_authorization_token)(
        volcenginesdkcr.GetAuthorizationTokenRequest(registry=host.split(".", 1)[0].removesuffix("-" + REGION))
    )
    auth = base64.b64encode(f"{token.username}:{token.token}".encode()).decode()
    inventory, failed = {}, []
    with tempfile.TemporaryDirectory() as docker_config:
        Path(docker_config, "config.json").write_text(json.dumps({"auths": {host: {"auth": auth}}}))
        env = os.environ.copy()
        env["DOCKER_CONFIG"] = docker_config
        # Upstream goes through the inherited proxy; the registry is reached directly.
        for key in ("NO_PROXY", "no_proxy"):
            env[key] = ",".join(filter(None, [env.get(key), host]))

        def run(image: str) -> tuple[str, dict]:
            target = mirror_image(image, registry)
            entry = {"target": target, "tasks": sorted(images[image])}
            try:
                entry["status"] = ensure(image, target, env)
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
                entry["status"], entry["error"] = "failed", str(error)
            print(f"{entry['status']:8} {image}", flush=True)
            return image, entry

        with ThreadPoolExecutor(max_workers=8) as pool:
            for image, entry in pool.map(run, sorted(images)):
                inventory[image] = entry
                if entry["status"] == "failed":
                    failed.append(image)
    out.write_text(json.dumps(inventory, indent=2) + "\n")
    counts = {status: sum(e["status"] == status for e in inventory.values()) for status in ("present", "copied")}
    print(f"Images: {counts['present']} present, {counts['copied']} copied, {len(failed)} failed; see {out}")
    if failed:
        raise RuntimeError(f"{len(failed)} images failed to mirror: {', '.join(failed)}")
