"""Run one job end to end and archive it: the unit of evaluation is experiments/<run>/, run = <UTC time>-<name>.

Usage: make job CONFIG=<config.yaml> (python job.py <config.yaml>); see example.yaml.
Steps: record code → mirror images → launch ECS workers → sync → Harbor (with profiles) → delete workers
→ upload the job to its archive. Workers are deleted even if the run fails or is stopped; only a SIGKILL
leaves them, tagged fibona-job=<run> in ECS.
"""

import getpass
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import traceback
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import volcenginesdkcore
import yaml
from steps import archive, code, profiles, workers
from steps.config import JobConfig, JobGroup, ssh_command
from steps.images import ensure_images


def now() -> str:
    return datetime.now(UTC).isoformat()


def launch_workers(config: JobConfig, run: str, job_dir: Path) -> list[JobGroup]:
    """Concrete host groups: fixed hosts as given, ECS groups launched, recording instances as soon as they exist."""
    ssh = ssh_command(config.settings, "")
    groups, launched = [], []
    for index, group in enumerate(config.groups):
        hosts = group.hosts
        if group.ecs:
            ids = workers.launch(run, index, group.ecs)
            launched += [{"id": i, "type": group.ecs.instance_type} for i in ids]
            (job_dir / "instances.json").write_text(json.dumps(launched, indent=2) + "\n")
            print(f"Launched {', '.join(ids)}; waiting for SSH...", flush=True)
            ips = workers.wait(ids, ssh)
            for instance, ip in zip(launched[-len(ids) :], ips, strict=True):
                instance["ip"] = ip
            (job_dir / "instances.json").write_text(json.dumps(launched, indent=2) + "\n")
            hosts = dict.fromkeys(ips, group.ecs.budget)
        groups.append(JobGroup(hosts=hosts, tasks=group.tasks))
    return groups


def run_harbor(config: JobConfig, job_dir: Path) -> int:
    """Harbor in a child process, teed to console.log, so stopping the job can cancel it and still clean up."""
    process = subprocess.Popen(
        [sys.executable, "-c", "from harbor.cli.main import app; app(prog_name='harbor')"]
        + config.harbor_args(job_dir),
        env={**os.environ, "PYTHONPATH": "."},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        with open(job_dir / "console.log", "a") as log:
            for line in process.stdout:
                sys.stdout.write(line)
                log.write(line)
                log.flush()
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
    configuration.region = workers.REGION
    volcenginesdkcore.Configuration.set_default(configuration)
    source = Path(sys.argv[1]).resolve()
    os.chdir(Path(__file__).resolve().parent)
    config = JobConfig.model_validate(yaml.safe_load(source.read_text()))
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
    # Turn Ctrl-C, `kill` or a lost terminal into an exception so the workers are still deleted and the job archived.
    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, stop_on_signal)

    exit_code, stop = 1, threading.Event()
    try:
        ensure_images(config, job_dir / "images.json")
        resolved = config.model_copy(update={"groups": launch_workers(config, run, job_dir)})
        (job_dir / "config.resolved.yaml").write_text(yaml.safe_dump(resolved.model_dump(mode="json"), sort_keys=False))
        # dist (our wheel, its dependency wheels, uv) and python go to /opt/fibona, which Harbor mounts.
        for host in resolved.hosts:
            ssh = shlex.join(resolved.ssh(host))
            subprocess.run(["rsync", "-a", "--delete", "-e", ssh, "dist", "python", f"{host}:/opt/fibona/"], check=True)
        threading.Thread(target=profiles.every_five_minutes, args=(resolved, job_dir, stop), daemon=True).start()
        exit_code = run_harbor(resolved, job_dir)
    except BaseException as error:
        meta["error"] = "".join(traceback.format_exception_only(error)).strip()
        traceback.print_exc()
    finally:
        ignore_signals()
        stop.set()
        meta["finished_at"], meta["exit_code"] = now(), exit_code
        if (job_dir / "instances.json").exists():
            meta["instances"] = json.loads((job_dir / "instances.json").read_text())
            try:
                print("Deleted", *workers.release(run), flush=True)
            except Exception as error:
                meta["release_error"] = str(error)
                traceback.print_exc()
        write_meta()
        try:
            print("Archived to", archive.upload(job_dir, config.archive), flush=True)
        except Exception:
            traceback.print_exc()
            exit_code = exit_code or 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
