"""Sync concrete distribution directories and verify the offline worker runtime before trials."""

import os
import shlex
import subprocess
from pathlib import Path

from steps.retry import network_retry, transient_output


def distribution_paths(root: Path) -> tuple[Path, Path]:
    dist, python = (root / name for name in ("dist", "python"))
    if not os.access(dist / "uv", os.X_OK) or not list(dist.glob("fibona_agent-*.whl")):
        raise ValueError("Build eval/dist first: executable uv and agent wheel are required")
    if not any(os.access(path, os.X_OK) for path in python.glob("*/bin/python3.12")):
        raise ValueError("eval/python must contain an executable Python 3.12 runtime")
    return dist.resolve(strict=True), python.resolve(strict=True)


@network_retry
def transfer(command: list[str]) -> None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired as error:
        raise TimeoutError("Worker transfer timed out") from error
    if result.returncode:
        if transient_output(result.stderr):
            raise ConnectionError("Transient worker transfer failure")
        raise RuntimeError(f"Worker deployment failed: {result.stderr}")


def deploy(config, root: Path) -> None:
    dist, python = distribution_paths(root)
    for host in config.hosts:
        ssh = shlex.join(config.ssh(host))
        # Trailing slashes dereference the top-level source link. Preserve internal
        # Python symlinks rather than exporting controller-only absolute links.
        for name, source in (("dist", dist), ("python", python)):
            transfer(["rsync", "-a", "--delete", "-e", ssh, f"{source}/", f"{host}:/opt/fibona/{name}/"])
        script = """set -eu
export UV_PYTHON_INSTALL_DIR=/opt/fibona/python
test -x /opt/fibona/dist/uv
/opt/fibona/dist/uv --version
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
/opt/fibona/dist/uv venv --offline --managed-python --python 3.12 "$work/venv"
/opt/fibona/dist/uv pip install --offline --no-index --find-links /opt/fibona/dist \
    --python "$work/venv/bin/python" /opt/fibona/dist/fibona_agent-*.whl
"$work/venv/bin/python" -c 'import core, openai; print("Worker runtime preflight passed")'
"""
        subprocess.run([*config.ssh(host), host, "bash -s"], input=script, text=True, check=True, timeout=180)
