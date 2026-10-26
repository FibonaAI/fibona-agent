import asyncio
import io
import math
import signal
import traceback
from collections.abc import Callable
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from threading import current_thread, main_thread
from typing import TextIO

from IPython.core.async_helpers import get_asyncio_loop
from IPython.core.interactiveshell import InteractiveShell
from openai import APIError
from traitlets.config import Config

from core.utils import log_cell

DEFAULT_CELL_TIMEOUT = 300


class CellTimeout(BaseException):
    """End a cell without being swallowed by its `except Exception` blocks."""


def cell_timeout(timeout: float | None) -> float:
    if timeout is None:
        return DEFAULT_CELL_TIMEOUT
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be a finite positive number of seconds, or null for 300 seconds")
    return timeout


@contextmanager
def pause_cell_timeout():
    """Exclude model/input waits on the cell's thread; worker threads do not own its timer."""
    timer = signal.setitimer(signal.ITIMER_REAL, 0) if current_thread() is main_thread() else None
    try:
        yield
    finally:
        if timer is not None:
            signal.setitimer(signal.ITIMER_REAL, *timer)


class Quit(BaseException):
    """Raised by `quit()` in a cell to end the run.

    A BaseException, so the `except Exception` blocks that cells often contain cannot swallow it.
    """


@dataclass
class CellResult:
    output: str  # Everything the cell printed to stdout/stderr.
    error: str | None = None  # Traceback if the cell failed.
    quit: bool = False  # The cell raised Quit.


class Tee(io.TextIOBase):
    """Write to several streams at once: show output live and capture it."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, text: str) -> int:
        for stream in self.streams:
            stream.write(text)
        return len(text)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


class IPythonEnv:
    """An in-process IPython session: runs cells and holds their variables (imports and
    functions too) across cells; supports top-level await and IPython magics.

    Output is always captured; optional streams also receive it live. User input requires
    an injected read_input handler, so the environment does not depend on a console.

    Cell deadlines are soft Unix main-thread interrupts; native code may delay them.
    A cell that crashes the interpreter still takes the agent down with it.
    """

    def __init__(
        self,
        *,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
        read_input: Callable[[str], str] | None = None,
    ):
        self.stdout, self.stderr = stdout, stderr
        self.read_input = pause_cell_timeout()(read_input if read_input is not None else _unavailable_input)
        # Memory keeps its own cell history, so skip IPython's (and its sqlite file in ~/.ipython).
        config = Config()
        config.HistoryAccessor.enabled = False
        self.shell = InteractiveShell(config=config)
        # Show only what cells print; a trailing expression is not echoed as `Out[n]: ...`.
        self.shell.ast_node_interactivity = "none"
        # Tracebacks go to CellResult.error instead of being printed into the output.
        self.shell._showtraceback = lambda *args, **kwargs: None
        # Quit is handled by `execute`, so IPython must not print or swallow it as an ordinary error.
        self.shell.set_custom_exc((Quit, CellTimeout), lambda *args, **kwargs: [])

    def bind(self, **names) -> None:
        """Put names (call_me, quit, ...) into the cells' global namespace, replacing old ones."""
        self.shell.user_ns.update(names)

    @log_cell
    def execute(self, code: str, *, timeout: float | None = None) -> CellResult:
        """Return cell output and errors; Quit ends the run, interrupts and API failures propagate."""
        timeout = cell_timeout(timeout)
        buffer = io.StringIO()
        self.bind(input=self.read_input)  # Rebound every cell, so a cell that replaced `input` does not persist.
        stdout = Tee(buffer) if self.stdout is None else Tee(self.stdout, buffer)
        stderr = Tee(buffer) if self.stderr is None else Tee(self.stderr, buffer)
        error, pending = None, None

        def on_timeout(signum, frame):
            nonlocal error
            error = CellTimeout(f"Cell exceeded {timeout:g} seconds. Narrow the operation or choose a longer timeout.")
            if pending is None or any(f.f_globals is self.shell.user_ns for f, _ in traceback.walk_stack(frame)):
                raise error
            # An awaiting cell is off the stack. Cancel its task, not the event loop.
            pending.get_loop().call_soon_threadsafe(pending.cancel)

        def track_async_cell(info):
            nonlocal pending
            pending = asyncio.current_task(loop=get_asyncio_loop())

        previous_handler = signal.signal(signal.SIGALRM, on_timeout)
        previous_timer = signal.getitimer(signal.ITIMER_REAL)
        self.shell.events.register("pre_run_cell", track_async_cell)
        try:
            signal.setitimer(signal.ITIMER_REAL, timeout)
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = self.shell.run_cell(code, store_history=False)
            error = error or result.error_before_exec or result.error_in_exec
        except (CellTimeout, ValueError, OverflowError) as exc:
            error = error or exc
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)
            self.shell.events.unregister("pre_run_cell", track_async_cell)
        if isinstance(error, KeyboardInterrupt) or (
            isinstance(error, APIError) and getattr(error, "status_code", None) not in {400, 422}
        ):
            # SDK retries are exhausted; a provider failure cannot be repaired by another model call.
            raise error
        if isinstance(error, Quit):
            return CellResult(buffer.getvalue(), quit=True)
        return CellResult(buffer.getvalue(), "".join(traceback.format_exception(error)) if error else None)


def _unavailable_input(prompt: str = "") -> str:
    raise RuntimeError("No input handler configured for this environment")
