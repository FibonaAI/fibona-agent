"""Fibona's bootstrap cell for Harbor tasks: the terminal bootstrap's working context without its UI, plus a cell timeout.

agent.py writes this file into the task container. It runs as a cell, so argv, call_me and get_ipython come from the
session. The task comes from the command line; nobody answers input(), so the agent ends the run with quit().
"""

import asyncio
import json
import math
import os
import signal
import threading
from functools import partial, wraps

from IPython.core.async_helpers import get_asyncio_loop

from bootstraps.terminal.context import initialize_context

# Cell timeout: SIGALRM ends a cell that runs too long, and core runs its repair cell as for any failed cell.
# Model calls and input() are not counted. The interrupt works like the terminal bootstrap's stop (see its init.py).
DEFAULT_TIMEOUT = 300


class CellTimeout(BaseException):
    """Raised in the timed-out cell; a BaseException, so `except Exception` cannot swallow it."""


_cell = {"timeout": DEFAULT_TIMEOUT, "task": None}  # The running cell's limit and, for an await cell, its task.


def cell_timeout(seconds: float) -> None:
    """Give the running cell `seconds` from now; call it first in a cell that needs longer than the default."""
    if isinstance(seconds, bool) or not isinstance(seconds, int | float) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("seconds must be a finite positive number")
    _cell["timeout"] = seconds
    signal.setitimer(signal.ITIMER_REAL, seconds)


def _on_timeout(signum, frame) -> None:
    # Cell code on the stack: raise there. An awaiting cell is off the stack: cancel its task, not the loop.
    # Otherwise IPython or a hook is running: do not cut it short, try again shortly.
    message = f"Cell exceeded {_cell['timeout']:g} seconds. Narrow the work, run it in the background, or call cell_timeout(seconds) first."
    while frame is not None:
        if frame.f_globals is globals() and frame.f_code not in _OWN_CODE:
            raise CellTimeout(message)
        frame = frame.f_back
    if (task := _cell["task"]) is not None:
        task.get_loop().call_soon_threadsafe(task.cancel, message)
    else:
        signal.setitimer(signal.ITIMER_REAL, 0.01)  # ponytail: 10 ms polling, only until the timeout lands.


def _paused(call):
    """`call` with its time on the cell's thread left out of the cell's timeout."""

    @wraps(call)
    def paused(*args, **kwargs):
        if threading.current_thread() is not threading.main_thread():
            return call(*args, **kwargs)  # Worker threads do not hold the cell's timer.
        timer = signal.setitimer(signal.ITIMER_REAL, 0)
        try:
            return call(*args, **kwargs)
        finally:
            signal.setitimer(signal.ITIMER_REAL, *timer)

    return paused


# The job's model settings (plugins/agent.py) as call_me defaults; a cell's own arguments win.
_CALL_ME_DEFAULTS = {}
if os.getenv("FIBONA_REASONING_EFFORT"):
    _CALL_ME_DEFAULTS["reasoning"] = {"effort": os.environ["FIBONA_REASONING_EFFORT"]}
if os.getenv("FIBONA_MAX_OUTPUT_TOKENS"):
    _CALL_ME_DEFAULTS["max_output_tokens"] = int(os.environ["FIBONA_MAX_OUTPUT_TOKENS"])

if os.getenv("FIBONA_EXTRA_BODY"):
    _extra_body = json.loads(os.environ["FIBONA_EXTRA_BODY"])
    if not isinstance(_extra_body, dict):
        raise ValueError("FIBONA_EXTRA_BODY must be a JSON object")
    _CALL_ME_DEFAULTS["extra_body"] = _extra_body


def _start_cell(info) -> None:
    namespace = globals()
    namespace["call_me"] = partial(namespace["call_me"], **_CALL_ME_DEFAULTS)
    for name in ("call_me", "call_llm_api", "input"):  # Core binds fresh ones before every cell.
        namespace[name] = _paused(namespace[name])
    _cell["timeout"], _cell["task"] = DEFAULT_TIMEOUT, asyncio.current_task(loop=get_asyncio_loop())
    signal.setitimer(signal.ITIMER_REAL, DEFAULT_TIMEOUT)


def _end_cell(result) -> None:
    signal.setitimer(signal.ITIMER_REAL, 0)
    _cell["task"] = None


_OWN_CODE = {f.__code__ for f in (_start_cell, _end_cell)}  # Hooks run in this namespace but are not cell code.
signal.signal(signal.SIGALRM, _on_timeout)
get_ipython().events.register("pre_run_cell", _start_cell)  # noqa: F821 -- provided by IPython.
get_ipython().events.register("post_run_cell", _end_cell)  # noqa: F821

instructions = """
You are running unattended in a benchmark container: nobody reads your output or answers input().
Before preparing final deliverables or declaring completion, reread startup_task in full and check every required output, path, format, behavior, and constraint against it.
Verify delivery by inspecting the actual files, database records, or service state and the results of relevant checks; a plan, summary, or successful file write alone does not establish completion.
Submit only one set_next_cell per wake; it schedules future work, which has not executed until recorded in history.
If a required deliverable is missing or a check fails, keep it pending and continue working. Call quit() only after verifying the task's requirements are satisfied.
These cells use Fibona's isolated Python runtime, which can differ from the task's Python.
Inspect the task's supplied interpreters and virtualenvs for preinstalled dependencies before downloading duplicate packages into this runtime.
The staged uv executable is /opt/fibona/dist/uv.
Stay within the task's declared memory budget, shared by Fibona, session objects, and child processes; leave headroom for the interpreter and SDK.
Inspect large objects using types, lengths, shapes, and small samples. Do not stringify entire globals(), history, model responses, or large objects before truncating them: repr(value)[:N] and str(value)[:N] still construct the full string first.
Write large results to files and keep paths and compact summaries in session state; avoid repeatedly embedding earlier history or output into new output.
For file discovery, start with paths explicitly provided by the task and the current working directory, then gradually widen the search.
Avoid unbounded recursive searches from the filesystem root; set time, depth, and result-count limits, do not follow directory symlinks, skip /proc, /sys, /dev, and read only regular files.
Every search command must have an explicit wall-clock timeout (for example, timeout 30s find ... or subprocess.run(..., timeout=30)); run potentially blocking Python searches in a subprocess with a timeout too.
If a search times out, narrow its scope or change methods.
Run programs expected to take a long time in separate processes, save their logs, and check progress periodically; avoid waiting indefinitely in a single cell.
Each cell gets 300 seconds of its own running time; model calls and input() do not count. Call cell_timeout(seconds) first in a cell that needs longer.
A timed-out cell fails and you repair from there; its side effects are kept.
""".strip()
task = " ".join(argv)  # noqa: F821 -- fibona.py binds the arguments it did not consume.
build_context = initialize_context(globals())
call_me(  # noqa: F821 -- core binds call_me.
    input=[*build_context(), {"role": "user", "content": "Start the user's task and submit the next Python cell."}],
    **_CALL_ME_DEFAULTS,
)
