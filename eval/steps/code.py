"""The code a job ran: HEAD plus git.patch, the uncommitted changes including untracked files .gitignore keeps."""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def git(*args: str, env: dict | None = None) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, env=env, check=True, capture_output=True, text=True).stdout


def record(job_dir: Path) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        index = Path(directory) / "index"
        shutil.copy(ROOT / git("rev-parse", "--git-path", "index").strip(), index)
        env = {**os.environ, "GIT_INDEX_FILE": str(index)}
        git("add", "-A", env=env)
        patch = git("diff", "--cached", "--binary", "HEAD", env=env)
    (job_dir / "git.patch").write_text(patch)
    return {
        "commit": git("rev-parse", "HEAD").strip(),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD").strip(),
        "dirty": bool(patch),
    }
