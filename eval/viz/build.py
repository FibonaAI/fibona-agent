"""Index a folder of jobs for the viz page: site/index.json, plus jobs/<run> for `harbor view`.

Usage: python3 viz/build.py [jobs folder, default experiments/] (from eval/; `make viz` does this). The folder holds
<run>/ directories as job.py writes them or as their TOS archives unpack. Standard library only; rerun after new jobs.
"""

import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

EVAL = Path(__file__).resolve().parent.parent
VIZ = EVAL / "viz"


def read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def seconds(start: str | None, end: str | None) -> float | None:
    if not (start and end):
        return None
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()


def summarize(run_dir: Path) -> dict | None:
    """One row per job: our meta.json plus Harbor's result.json; one per trial from its own result.json."""
    meta = read(run_dir / "meta.json")
    result = read(run_dir / "jobs" / run_dir.name / "result.json")
    harbor = read(run_dir / "jobs" / run_dir.name / "config.json")  # Model and dataset for jobs without meta.json.
    if not meta and not result:
        return None
    trials = {}
    for trial_dir in sorted((run_dir / "jobs" / run_dir.name).glob("*__*/")):
        trial = read(trial_dir / "result.json")
        execution, agent = trial.get("agent_execution") or {}, trial.get("agent_result") or {}
        trials[trial_dir.name] = {
            "reward": ((trial.get("verifier_result") or {}).get("rewards") or {}).get("reward"),
            "exception": (trial.get("exception_info") or {}).get("exception_type"),
            "agent_seconds": seconds(execution.get("started_at"), execution.get("finished_at")),
            "cost_usd": agent.get("cost_usd"),
        }
    git = meta.get("git", {})
    # The git user, else the system one: launched_by is {"user", "host", "git": "Name <email>"}, or "user@host" before.
    who = meta.get("launched_by") or ""
    if isinstance(who, dict):
        who = (who.get("git") or "").split(" <")[0] or who.get("user")
    else:
        who = who.split("@")[0] or None
    return {
        "run": run_dir.name,
        "dataset": meta.get("dataset") or next((d.get("name") for d in harbor.get("datasets", [])), None),
        "model": meta.get("model") or next((a.get("model_name") for a in harbor.get("agents", [])), None),
        "commit": git.get("commit"),
        "branch": git.get("branch"),
        "dirty": git.get("dirty"),
        "user": who,
        "started_at": meta.get("started_at") or result.get("started_at"),
        "finished_at": meta.get("finished_at") or result.get("finished_at"),
        "exit_code": meta.get("exit_code"),
        "error": meta.get("error"),
        "n_trials": result.get("n_total_trials"),
        "n_errored": result.get("stats", {}).get("n_errored_trials"),
        "n_passed": sum((t["reward"] or 0) >= 1 for t in trials.values()),
        "trials": [{"name": name, **trial} for name, trial in sorted(trials.items())],
        "viewable": (run_dir / "jobs" / run_dir.name / "config.json").exists(),
    }


def main() -> None:
    source = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else EVAL / "experiments"
    runs = [row for d in sorted(source.glob("*/"), reverse=True) if (row := summarize(d))]
    (VIZ / "site").mkdir(exist_ok=True)
    (VIZ / "site" / "index.json").write_text(json.dumps(runs, ensure_ascii=False) + "\n")
    # harbor view wants one folder of job directories (ours sit at experiments/<run>/jobs/<run>/) and rejects
    # symlinks that resolve outside it, so mirror each job as hard links: real directories, no extra disk space.
    # The folder must be on the same filesystem as viz/.
    mirror = VIZ / "jobs"
    shutil.rmtree(mirror, ignore_errors=True)  # Only links: the jobs themselves stay.
    mirror.mkdir()
    for row in runs:
        if row["viewable"]:
            job = source / row["run"] / "jobs" / row["run"]
            # Files we cannot read (e.g. root-owned logs) cannot be linked; skip them, as the viewer could not read them either.
            subprocess.run(["cp", "-al", str(job), str(mirror / row["run"])], stderr=subprocess.DEVNULL)
    # job.py's own files per run (run.html picks which to show), hard-linked like the job mirror.
    files = VIZ / "site" / "runs"
    shutil.rmtree(files, ignore_errors=True)
    for row in runs:
        (files / row["run"]).mkdir(parents=True)
        for path in (source / row["run"]).iterdir():
            try:
                (files / row["run"] / path.name).hardlink_to(path)
            except OSError:  # Directories, or files not ours to link (root-owned).
                pass
    print(f"Indexed {len(runs)} jobs into {VIZ / 'site' / 'index.json'}")


if __name__ == "__main__":
    main()
