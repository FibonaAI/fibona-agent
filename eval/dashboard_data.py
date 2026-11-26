"""Build dashboard summaries from local Harbor results without reading agent trajectories."""

import json
from datetime import datetime
from pathlib import Path


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def duration(result, stage):
    times = result.get(stage) or {}
    if not times.get("started_at") or not times.get("finished_at"):
        return None
    return round(
        (
            datetime.fromisoformat(times["finished_at"].replace("Z", "+00:00"))
            - datetime.fromisoformat(times["started_at"].replace("Z", "+00:00"))
        ).total_seconds()
    )


def trial_summary(config_path):
    directory = config_path.parent
    config = read_json(config_path)
    result = read_json(directory / "result.json")
    reward = ((result.get("verifier_result") or {}).get("rewards") or {}).get("reward")
    exception = result.get("exception_info") or {}
    usage = result.get("agent_result") or {}
    details = read_json(directory / "verifier" / "reward-details.json")
    criteria = (details.get("reward") or {}).get("criteria", [])
    checks = None
    if details and isinstance(criteria, list):
        checks = {
            "passed": sum(item.get("value") == 1 for item in criteria),
            "failed": sum(item.get("value") == 0 for item in criteria),
        }
    return {
        "id": directory.name,
        "task": result.get("task_name") or config.get("task", {}).get("name") or directory.name.split("__")[0],
        "reward": reward,
        "exception": exception.get("exception_type"),
        "exceptionMessage": exception.get("exception_message"),
        "checkStats": checks,
        "status": "error" if exception else "success" if reward == 1 else "failed" if reward == 0 else "running",
        "startedAt": result.get("started_at"),
        "finishedAt": result.get("finished_at"),
        "setupSec": duration(result, "agent_setup"),
        "agentSec": duration(result, "agent_execution"),
        "verifierSec": duration(result, "verifier"),
        "inputTokens": usage.get("n_input_tokens"),
        "outputTokens": usage.get("n_output_tokens"),
        "cost": usage.get("cost_usd"),
    }


def build_jobs(root: Path):
    jobs = []
    for config_path in root.glob("*/config.json"):
        config = read_json(config_path)
        result = read_json(config_path.parent / "result.json")
        if not config or not result:
            continue
        stats = result.get("stats") or {}
        agent = (config.get("agents") or [{}])[0]
        passed, failed, exceptions = [], [], []
        mean = None
        for evaluation in (stats.get("evals") or {}).values():
            rewards = (evaluation.get("reward_stats") or {}).get("reward") or {}
            passed.extend(trial.split("__")[0] for trial in rewards.get("1.0", []))
            failed.extend(trial.split("__")[0] for trial in rewards.get("0.0", []))
            for kind, trials in (evaluation.get("exception_stats") or {}).items():
                exceptions.extend({"type": kind, "task": trial.split("__")[0]} for trial in trials)
            if evaluation.get("metrics"):
                mean = evaluation["metrics"][0].get("mean")
        tasks = [task["name"] for task in config.get("tasks", []) if task.get("name")]
        if not tasks:
            tasks = [name for dataset in config.get("datasets", []) for name in dataset.get("task_names", [])]
        trials = []
        for trial_config in config_path.parent.glob("*/config.json"):
            try:
                trials.append(trial_summary(trial_config))
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
        jobs.append(
            {
                "id": config_path.parent.name,
                "startedAt": result.get("started_at"),
                "finishedAt": result.get("finished_at"),
                "harness": agent.get("name", "?"),
                "model": agent.get("model_name", "?"),
                "total": result.get("n_total_trials", 0),
                "completed": stats.get("n_completed_trials", 0),
                "running": stats.get("n_running_trials", 0),
                "pending": stats.get("n_pending_trials", 0),
                "cancelled": stats.get("n_cancelled_trials", 0),
                "errors": stats.get("n_errored_trials", 0),
                "retries": stats.get("n_retries", 0),
                "mean": mean,
                "passedCount": len(passed),
                "failedCount": len(failed),
                "passed": sorted(set(passed)),
                "failed": sorted(set(failed)),
                "exceptions": exceptions,
                "tasks": tasks,
                "trials": sorted(trials, key=lambda trial: trial.get("startedAt") or "", reverse=True),
                "status": "running"
                if not result.get("finished_at")
                else "error"
                if stats.get("n_errored_trials", 0)
                else "complete",
                "cost": stats.get("cost_usd"),
            }
        )
    return sorted(jobs, key=lambda job: job.get("startedAt") or "", reverse=True)
