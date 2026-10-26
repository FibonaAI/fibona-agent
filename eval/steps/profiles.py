"""Worker resource snapshots in experiments/<run>/profiles/; Harbor records evaluation progress.

This file also runs on each worker as `python3 -c <this file> --worker`, so it imports only the standard library.
"""

import json
import shlex
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

COMMANDS = {
    "load": ["uptime"],
    "memory": ["free", "-b"],
    "disk": ["df", "-B1", "-x", "tmpfs", "-x", "devtmpfs"],
    "containers": ["docker", "ps", "--format", "{{.ID}} | {{.Names}} | {{.Image}} | {{.Status}}"],
    "container_stats": ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
    "gpu": [
        "nvidia-smi",
        "--query-gpu=index,driver_version,utilization.gpu,memory.total,memory.used,power.draw",
        "--format=csv,noheader,nounits",
    ],
}


def resources() -> dict:
    result = {}
    for name, command in COMMANDS.items():
        try:
            run = subprocess.run(command, text=True, capture_output=True, timeout=12)
            result[name] = {
                "exit": run.returncode,
                "stdout": "\n".join(run.stdout.splitlines()[:100]),
                "stderr": run.stderr[:1000],
            }
        except (OSError, subprocess.TimeoutExpired) as error:
            result[name] = {"error": type(error).__name__}
    return result


def snapshot(config, job_dir: Path) -> Path:
    from datetime import UTC, datetime

    script = Path(__file__).read_text()

    def worker(host: str) -> tuple[str, dict]:
        try:
            result = subprocess.run(
                [*config.ssh(host), "-n", host, "python3 -c " + shlex.quote(script) + " --worker"],
                text=True,
                capture_output=True,
                timeout=100,
                check=True,
            )
            return host, json.loads(result.stdout)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            return host, {"error": type(error).__name__}

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=len(config.hosts)) as pool:
        workers = dict(pool.map(worker, config.hosts))
    now = datetime.now(UTC)
    target = job_dir / "profiles" / (now.strftime("%Y%m%dT%H%M%SZ") + ".json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "at": now.isoformat(),
                "collection_seconds": round(time.monotonic() - started, 2),
                "workers": workers,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"profile {target}", flush=True)
    return target


def every_five_minutes(config, job_dir: Path, stop: threading.Event) -> None:
    while True:
        try:
            snapshot(config, job_dir)
        except Exception:
            traceback.print_exc()
        if stop.wait(300):
            return


if __name__ == "__main__" and sys.argv[1:] == ["--worker"]:
    print(json.dumps(resources()))
