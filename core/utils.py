import json
import logging
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from uuid import uuid4


class JsonFormatter(logging.Formatter):
    """Keep the log's flat JSONL event format."""

    def __init__(self, run_id):
        super().__init__()
        self.run_id = run_id

    def format(self, record):
        return json.dumps(
            {
                "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
                "run_id": self.run_id,
                "cell": record.cell,
                **record.msg,
            },
            ensure_ascii=False,
            default=lambda value: value.model_dump(mode="json") if hasattr(value, "model_dump") else str(value),
        )


@contextmanager
def run_logger():
    """Open one run's JSONL log and release its file handler on exit."""
    run_id = uuid4().hex
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{run_id}.jsonl"
    log_path.touch(mode=0o600, exist_ok=False)
    with ExitStack() as cleanup:
        handler = logging.FileHandler(log_path, encoding="utf-8")
        cleanup.callback(handler.close)
        handler.setFormatter(JsonFormatter(run_id))
        logger = logging.Logger("fibona.run", level=logging.INFO)
        logger.addHandler(handler)
        cleanup.callback(logger.removeHandler, handler)
        yield logger


def log_wake(wake):
    """Record one wake's initial arguments and final result when a logger is bound."""

    @wraps(wake)
    def wrapped(self, input=(), *, submit, logger=None, **body):
        if logger is None:
            return wake(self, input=input, submit=submit, **body)
        started_at, wake_id = datetime.now(UTC).isoformat(), uuid4().hex
        request = {k: v for k, v in body.items() if k != "extra_headers"}
        request["input"] = input
        response, error = None, None
        try:
            request.update(model=self.model, instructions=self.instructions)
            response = wake(self, input=input, submit=submit, **body)
            return response
        except BaseException as exc:
            error = {"type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            logger.info(
                {
                    "event": "call_me",
                    "wake_id": wake_id,
                    "started_at": started_at,
                    "request": request,
                    "response": response,
                    "error": error,
                }
            )

    return wrapped


def log_cell(execute):
    """Record a cell before execution, including cells that fail or are interrupted."""

    @wraps(execute)
    def wrapped(self, code, *, logger=None, summary=""):
        if logger is not None:
            logger.info({"event": "cell", "summary": summary, "code": code})
        return execute(self, code)

    return wrapped
