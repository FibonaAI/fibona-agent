"""Run one job end to end and archive it: the unit of evaluation is experiments/<run>/, run = <UTC time>-<name>.

Usage: make job CONFIG=<config.yaml> (python job.py <config.yaml>); see local.example.yaml and cluster.*.example.yaml.
Steps: record code → mirror images → launch ECS workers (+ vePFS) → sync → Harbor (with profiles) → delete workers
→ upload the job to its archive. A local job has no workers: it syncs to a temporary directory here and runs
trials in this machine's Docker. Workers are deleted even if the run fails or is stopped; only a SIGKILL
leaves them, tagged fibona-job=<run> in ECS.
"""

import getpass
import json
import os
import secrets
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import volcenginesdkcore
import yaml
from steps import archive, code, profiles, workers
from steps.config import JobConfig, JobGroup, ssh_command
from steps.images import ensure_images

VIEW = "http://harbor.f.x/jobs/"  # Where `make viz` serves archived jobs by run name.


def now() -> str:
    return datetime.now(UTC).isoformat()


def took(started: float) -> str:
    seconds = round(time.monotonic() - started)
    return f"{seconds // 60}m{seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def launch_workers(config: JobConfig, run: str, job_dir: Path, log: Callable[[str], None]) -> list[JobGroup]:
    """Concrete host groups: fixed hosts checked over SSH, ECS groups launched, recording instances as soon as they
    exist. log(message) gets one line per host or instance event."""
    groups, launched, public = [], [], config.region != workers.REGION
    for index, group in enumerate(config.groups):
        hosts = group.hosts
        for host in hosts:
            check = subprocess.run(
                [*config.ssh(host), "-o", "ConnectTimeout=10", "-n", host, "true"], capture_output=True, text=True
            )
            if check.returncode:
                raise RuntimeError(f"No SSH to {host}: {check.stderr.strip()}")
            log(f"{host}  fixed host, reachable over SSH")
        if group.ecs:
            spec = group.ecs
            log(f"group {index}: requesting {spec.count} x {spec.instance_type} in {spec.zone}")
            ids = workers.launch(run, index, spec)
            launched += [{"id": i, "type": spec.instance_type} for i in ids]
            (job_dir / "instances.json").write_text(json.dumps(launched, indent=2) + "\n")
            log(f"created {', '.join(ids)}")
            pairs = workers.wait(ids, ssh_command(config.settings, ""), log, public)
            for instance, (ip, eip) in zip(launched[-len(ids) :], pairs, strict=True):
                instance.update(ip=ip, eip=eip, host=eip if public else ip)  # host: where SSH and Harbor go.
            (job_dir / "instances.json").write_text(json.dumps(launched, indent=2) + "\n")
            hosts = dict.fromkeys((instance["host"] for instance in launched[-len(ids) :]), group.ecs.budget)
        groups.append(JobGroup(hosts=hosts, tasks=group.tasks))
    if config.vepfs and launched:
        # The client-mount nodes log in to the workers as root with it; ECS wants upper and lower case, digits and a
        # symbol, at most 30 characters.
        started, root_password = time.monotonic(), f"Fb{secrets.token_hex(12)}9!"
        ips, hosts = [instance["ip"] for instance in launched], [instance["host"] for instance in launched]
        for host in hosts:
            run_ssh = [*config.ssh(host), host, "sudo chpasswd"]
            subprocess.run(run_ssh, input=f"root:{root_password}\n", text=True, capture_output=True, check=True)
        service, home = config.vepfs, shlex.quote(f"/vepfs/{workers.HOMES[config.region]}")
        log(f"vepfs: {service} joins {len(ips)} workers and mounts /vepfs, output in vepfs.log")
        # To the client-mount nodes through a worker, in every region: outside cn-beijing this machine cannot reach
        # their private IPs, and one path keeps the vePFS steps the same everywhere (see steps/workers.py).
        via = shlex.join([*config.ssh(hosts[0]), "-W", "%h:%p", hosts[0]])
        (job_dir / "vepfs.log").write_text(
            workers.on_mount_node(service, f"vepfs -p '{root_password}' add password {' '.join(ips)}", via)
        )
        bind = (
            f"findmnt -t gpfs -no SIZE /vepfs && sudo mkdir -p /home/users && sudo mount --bind {home} /home/users"
            " && test -w /home/users/shared"
        )
        for host in hosts:
            mounted = subprocess.run([*config.ssh(host), "-n", host, bind], capture_output=True, text=True)
            if mounted.returncode:
                raise RuntimeError(f"{host}: /vepfs or writable /home/users/shared missing: {mounted.stderr.strip()}")
            log(f"{host}  /vepfs mounted, {mounted.stdout.strip()}; {home} at /home/users ({took(started)})")
    return groups


def run_harbor(config: JobConfig, job_dir: Path, mount_source: str) -> int:
    """Harbor in a child process, so stopping the job can cancel it and still clean up. It writes to our terminal
    directly: its live progress only renders on a TTY, and its own log is jobs/<run>/job.log."""
    process = subprocess.Popen(
        [sys.executable, "-c", "from harbor.cli.main import app; app(prog_name='harbor')"]
        + config.harbor_args(job_dir, mount_source),
        env={**os.environ, "PYTHONPATH": "."},
    )
    try:
        return process.wait()
    finally:
        if process.poll() is None:
            # Harbor cancels its trials on SIGTERM; don't wait forever on hosts we are about to delete.
            process.terminate()
            try:
                process.wait(timeout=120)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def ignore_signals() -> None:
    """From the first stop on, further signals (uv forwards each one, users repeat Ctrl-C) must not cut
    stopping Harbor, deleting workers or archiving short; SIGKILL still can."""
    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, signal.SIG_IGN)


def stop_on_signal(signum, _) -> None:
    ignore_signals()
    sys.exit(128 + signum)


def main() -> int:
    # SDK clients sign for this region with VOLCENGINE_ACCESS_KEY/VOLCENGINE_SECRET_KEY from the environment.
    configuration = volcenginesdkcore.Configuration()
    source = Path(sys.argv[1]).resolve()
    os.chdir(Path(__file__).resolve().parent)
    config = JobConfig.model_validate(yaml.safe_load(source.read_text()))
    configuration.region = config.region
    volcenginesdkcore.Configuration.set_default(configuration)
    run = config.run_name()
    try:
        who = code.git("config", "user.name").strip() + " <" + code.git("config", "user.email").strip() + ">"
    except subprocess.CalledProcessError:
        sys.exit("Set git user.name and user.email: every job records who ran it.")
    job_dir = Path("experiments") / run
    if job_dir.exists():
        sys.exit(f"{job_dir} exists; archives are never overwritten.")
    job_dir.mkdir(parents=True)
    shutil.copy(source, job_dir / "config.yaml")
    meta = {
        "run": run,
        "dataset": config.dataset,
        "model": config.model,
        "config": str(source),
        "started_at": now(),
        "git": code.record(job_dir),
        "harbor": version("harbor"),
        "launched_by": {
            "user": getpass.getuser(),
            "host": socket.gethostname(),
            "git": who,  # Who, not which account: shared machines and accounts run jobs too.
        },
    }
    write_meta = lambda: (job_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")  # noqa: E731
    write_meta()

    def log(message: str, stage: str = "") -> None:
        """One timestamped line per event, to the terminal and experiments/<run>/job.log (Harbor's own log is
        jobs/<run>/job.log). stage labels a stage's first line; detail lines leave it empty."""
        lines = message.splitlines() or [""]
        text = "".join(
            f"{datetime.now():%H:%M:%S}  {stage if n == 0 else '':<16}{line}\n" for n, line in enumerate(lines)
        )
        sys.stdout.write(text)
        sys.stdout.flush()
        with open(job_dir / "job.log", "a") as file:
            file.write(text)

    stages = ["images", "sync", "harbor", "cleanup"]
    if not config.local:
        stages.insert(1, "hosts")
    step = {name: f"[{n}/{len(stages)}] {name}" for n, name in enumerate(stages, 1)}

    started = time.monotonic()
    effort = config.model_settings.reasoning_effort
    log(run, "job")
    for key, value in [
        ("mode", "local" if config.local else "cluster"),  # Hosts and workers show up in their stage.
        ("dataset", config.dataset),
        ("model", config.model + (f", effort {effort}" if effort else "")),
        ("registry", config.registry or "none, upstream"),
        ("results", f"{job_dir}/ (Harbor's under jobs/{run}/)"),
        ("viewer", f"{VIEW}{run} (after archive)"),
    ]:
        log(f"{key:<9}{value}")
    # Turn Ctrl-C, `kill` or a lost terminal into an exception so the workers are still deleted and the job archived.
    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, stop_on_signal)

    docker = ["docker", "info", "--format", "{{json .SecurityOptions}}"]
    if config.local and "name=rootless" in subprocess.run(docker, capture_output=True, text=True).stdout:
        # Harbor chowns trial logs to container root for rootless Docker, which is us only if the daemon runs as us.
        log(
            "this machine's Docker is rootless. Unless its daemon runs as you, files the task containers write "
            "(agent logs, verifier output) are not yours: reading agent logs fails the trials and archiving fails.",
            "warning",
        )

    exit_code, stop = 1, threading.Event()
    # A local job's copy of dist and python, so another job rebuilding dist cannot change it mid-run.
    mount_source = tempfile.mkdtemp(prefix=f"fibona-{run}-") if config.local else "/opt/fibona"
    try:
        log(f"checking task images in {config.registry}" if config.registry else "skipped", step["images"])
        ensure_images(config, job_dir / "images.json", log)
        if not config.local:
            log("fixed hosts and ECS workers", step["hosts"])
        resolved = config.model_copy(update={"groups": launch_workers(config, run, job_dir, log)})
        (job_dir / "config.resolved.yaml").write_text(yaml.safe_dump(resolved.model_dump(mode="json"), sort_keys=False))
        # dist (our wheel, its dependency wheels, uv) and python go to mount_source, which Harbor mounts at /opt/fibona.
        log("dist and python, mounted at /opt/fibona in trials", step["sync"])
        for host in resolved.hosts:
            stage = time.monotonic()
            ssh = shlex.join(resolved.ssh(host))
            target = f"{host}:{mount_source}/"
            subprocess.run(["rsync", "-a", "--delete", "-e", ssh, "dist", "python", target], check=True)
            log(f"{host}  done ({took(stage)})")
        if config.local:
            os.chmod(mount_source, 0o755)  # Readable by the task containers' agent user, as /opt/fibona is on workers.
            subprocess.run(["rsync", "-a", "--delete", "dist", "python", f"{mount_source}/"], check=True)
            log(f"{mount_source}  done")
        threading.Thread(target=profiles.every_five_minutes, args=(resolved, job_dir, stop), daemon=True).start()
        log("started; live progress below", step["harbor"])
        stage = time.monotonic()
        exit_code = run_harbor(resolved, job_dir, mount_source)
        log(f"exited {exit_code} ({took(stage)})", step["harbor"])
    except BaseException as error:
        meta["error"] = "".join(traceback.format_exception_only(error)).strip()
        log(traceback.format_exc().rstrip(), "error")
    finally:
        ignore_signals()
        stop.set()
        if config.local:
            shutil.rmtree(mount_source, ignore_errors=True)
        meta["finished_at"], meta["exit_code"] = now(), exit_code
        log("deleting workers, archiving", step["cleanup"])
        if (job_dir / "instances.json").exists():
            meta["instances"] = json.loads((job_dir / "instances.json").read_text())
            joined = [instance for instance in meta["instances"] if "host" in instance]
            if config.vepfs and joined:
                try:
                    ips, hosts = [instance["ip"] for instance in joined], [instance["host"] for instance in joined]
                    for host in hosts:  # The bind mount would keep `vepfs del` from unmounting /vepfs.
                        subprocess.run([*config.ssh(host), "-n", host, "sudo umount /home/users"], capture_output=True)
                    via = shlex.join([*config.ssh(hosts[0]), "-W", "%h:%p", hosts[0]])  # As for `vepfs add`.
                    with open(job_dir / "vepfs.log", "a") as file:
                        file.write(workers.on_mount_node(config.vepfs, f"vepfs del {' '.join(ips)}", via))
                    log(f"vepfs: {', '.join(ips)} left {config.vepfs}")
                except Exception as error:
                    meta["vepfs_error"] = str(error)
                    log(traceback.format_exc().rstrip(), "error")
            try:
                for instance in workers.release(run):
                    log(f"{instance}  deleted")
            except Exception as error:
                meta["release_error"] = str(error)
                log(traceback.format_exc().rstrip(), "error")
        write_meta()
        try:
            log(f"archived to {archive.upload(job_dir, config.archive)}")
            log(f"exit {exit_code} after {took(started)}; view at {VIEW}{run}", "done")
        except Exception:
            log(traceback.format_exc().rstrip(), "error")
            exit_code = exit_code or 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
