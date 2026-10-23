import json
import logging
from contextlib import contextmanager
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
    logger = logging.Logger("fibona.run", level=logging.INFO)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    try:
        handler.setFormatter(JsonFormatter(run_id))
        logger.addHandler(handler)
        yield logger
    finally:
        logger.removeHandler(handler)
        handler.close()


def log_event(logger, event, **data):
    if logger is not None:
        logger.info({"event": event, **data})


def log_cell(execute):
    """Record a cell before execution, including cells that fail or are interrupted."""

    @wraps(execute)
    def wrapped(self, code, *, logger=None, summary=""):
        log_event(logger, "cell", summary=summary, code=code)
        return execute(self, code)

    return wrapped
