import json
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from threading import Lock
from uuid import uuid4


def create_run_log(path, *, key):
    """Create a thread-safe JSONL writer shared by a run's logging decorators."""
    log_path = Path(path).expanduser().resolve()
    log_path.touch(mode=0o600)
    run_id, log_lock = uuid4().hex, Lock()

    def log(event: str, cell: int, **data) -> None:
        record = {"time": datetime.now(UTC).isoformat(), "run_id": run_id, "cell": cell, "event": event, **data}
        line = json.dumps(
            record,
            ensure_ascii=False,
            default=lambda value: value.model_dump(mode="json") if hasattr(value, "model_dump") else str(value),
        )
        if isinstance(key, str) and key:
            line = line.replace(json.dumps(key, ensure_ascii=False)[1:-1], "[REDACTED]")
        with log_lock, log_path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")

    return log


def log_wake(wake):
    """Record one wake's initial arguments and final result when a logger is bound."""

    @wraps(wake)
    def wrapped(self, input=(), *, submit, log=None, **body):
        if log is None:
            return wake(self, input=input, submit=submit, **body)
        started_at, wake_id = datetime.now(UTC).isoformat(), uuid4().hex
        request = {k: v for k, v in body.items() if k != "extra_headers"}
        request.update(input=input, model=self.model, host_instructions=self.instructions)
        response, error = None, None
        try:
            response = wake(self, input=input, submit=submit, **body)
            return response
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            log(wake_id=wake_id, started_at=started_at, request=request, response=response, error=error)

    return wrapped


def log_cell(execute):
    """Record a cell before execution, including cells that fail or are interrupted."""

    @wraps(execute)
    def wrapped(self, code, *, log=None, summary=""):
        if log is not None:
            log(summary=summary, code=code)
        return execute(self, code)

    return wrapped
