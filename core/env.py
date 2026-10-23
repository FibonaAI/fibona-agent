import asyncio
import io
import signal
import threading
import traceback
from collections.abc import Callable, Coroutine
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from types import FrameType
from typing import Any, TextIO

from IPython.core.async_helpers import get_asyncio_loop
from IPython.core.interactiveshell import ExecutionResult, InteractiveShell
from traitlets.config import Config

from core.utils import log_cell


class Quit(BaseException):
    """Raised by `quit()` in a cell to end the run.

    A BaseException, so the `except Exception` blocks that cells often contain cannot swallow it.
    """


STOPPED = "Stopped by the user."  # The error recorded for a stopped cell.


class Stop(BaseException):
    """Raised in the running cell when the user stops it (SIGUSR1, see IPythonEnv); the run waits for them.

    A BaseException for the same reason as Quit.
    """


@dataclass
class CellResult:
    output: str  # Everything the cell printed to stdout/stderr.
    error: str | None = None  # Traceback if the cell failed.
    quit: bool = False  # The cell raised Quit.
    stopped: bool = False  # The user stopped the cell.


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

    A cell that hangs or crashes the interpreter takes the agent down with it.

    Stop: `signal.pthread_kill(threading.main_thread().ident, signal.SIGUSR1)`, from any thread, stops the
    running cell (or the next one, sent between cells), even inside a blocking call, but not inside native code.
    POSIX only, for cells on the main thread. The first execute() installs the handler, owned by the latest env
    to execute; before it, SIGUSR1 kills the process. Not SIGINT: Ctrl+C ends the whole run.
    """

    def __init__(
        self,
        *,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
        read_input: Callable[[str], str] | None = None,
    ):
        self.stdout, self.stderr = stdout, stderr
        self.read_input = read_input if read_input is not None else _unavailable_input
        self.cell_running = False  # A cell executes on the main thread, where a stop can interrupt it.
        self.cell_async_task: asyncio.Task[ExecutionResult] | None = None  # Task of a top-level await cell.
        self.stop_requested = False  # Set by SIGUSR1, until a cell ends stopped.
        # Memory keeps its own cell history, so skip IPython's (and its sqlite file in ~/.ipython).
        config = Config()
        config.HistoryAccessor.enabled = False
        self.shell = InteractiveShell(config=config)
        # Show only what cells print; a trailing expression is not echoed as `Out[n]: ...`.
        self.shell.ast_node_interactivity = "none"
        # Tracebacks go to CellResult.error instead of being printed into the output.
        self.shell._showtraceback = lambda *args, **kwargs: None
        # Quit is handled by `execute`, so IPython must not print or swallow it as an ordinary error.
        self.shell.set_custom_exc((Quit, Stop), lambda *args, **kwargs: [])
        # Run top-level await cells as a task a stop can cancel; IPython's default runner keeps no handle on it.
        self.shell.loop_runner = self._run_async_cell

    def bind(self, **names) -> None:
        """Put names (call_me, quit, ...) into the cells' global namespace, replacing old ones."""
        self.shell.user_ns.update(names)

    def _signal_cell(self) -> None:
        """Signal the cell again, while the request stands and the cell runs."""
        if self.stop_requested and self.cell_running:
            signal.pthread_kill(threading.main_thread().ident, signal.SIGUSR1)

    def _raise_stop(self, signum: int, frame: FrameType | None) -> None:
        # The frame walk decides how to stop, not just whether:
        # - cell code on the stack (frames whose globals are the session's): raise Stop there.
        # - an awaiting cell is off the stack, the loop's select() is: raising would break the loop and leave the
        #   cell's task pending, to resume in the next async cell; cancel the task instead, as ipykernel does.
        # - otherwise IPython or a host hook (the terminal's pre/post_run_cell) is running: do not leave that code
        #   half done; signal again shortly, until the stop lands in the cell's code or the cell ends and the next
        #   execute() takes the request.
        self.stop_requested = True  # The signal is the request, whoever sent it; between cells it waits.
        if not self.cell_running:
            return
        while frame is not None:
            if frame.f_globals is self.shell.user_ns:
                raise Stop
            frame = frame.f_back
        task = self.cell_async_task
        if task is not None:
            # The handler interrupted the loop itself, so ask the loop to cancel rather than cancel here; the
            # wakeup ends its select(), and the task gets CancelledError at the cell's await, then unwinds.
            task.get_loop().call_soon_threadsafe(task.cancel)
        else:
            threading.Timer(0.01, self._signal_cell).start()  # ponytail: 10 ms polling, only while hooks run.

    def _run_async_cell(self, coro: Coroutine[Any, Any, ExecutionResult]) -> ExecutionResult:
        """IPython's loop runner: run_until_complete, keeping the task for a stop to cancel."""
        loop = get_asyncio_loop()
        self.cell_async_task = loop.create_task(coro)
        try:
            return loop.run_until_complete(self.cell_async_task)
        finally:
            self.cell_async_task = None

    @log_cell
    def execute(self, code: str) -> CellResult:
        """Return cell output and errors; Quit sets the quit flag, Stop the stopped flag, KeyboardInterrupt propagates."""
        buffer = io.StringIO()
        self.bind(input=self.read_input)  # Rebound every cell, so a cell that replaced `input` does not persist.
        stdout = Tee(buffer) if self.stdout is None else Tee(self.stdout, buffer)
        stderr = Tee(buffer) if self.stderr is None else Tee(self.stderr, buffer)
        if threading.current_thread() is threading.main_thread():  # Python runs signal handlers there only.
            signal.signal(signal.SIGUSR1, self._raise_stop)
            self.cell_running = True
        try:
            if self.stop_requested:  # Requested between cells: this cell does not run.
                raise Stop
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = self.shell.run_cell(code, store_history=False)
            error = result.error_before_exec or result.error_in_exec
        except Stop as stop:
            # Requested between cells, or raised in a hook defined in a cell (outside the cell's exec).
            error = stop
        finally:
            self.cell_running = False
        if isinstance(error, KeyboardInterrupt):
            raise error  # Ctrl+C is the user stopping the agent, not a bug for it to repair.
        if isinstance(error, Quit):
            return CellResult(buffer.getvalue(), quit=True)
        if isinstance(error, Stop) or (isinstance(error, asyncio.CancelledError) and self.stop_requested):
            self.stop_requested = False
            return CellResult(buffer.getvalue(), STOPPED, stopped=True)
        return CellResult(buffer.getvalue(), "".join(traceback.format_exception(error)) if error else None)


def _unavailable_input(prompt: str = "") -> str:
    raise RuntimeError("No input handler configured for this environment")
