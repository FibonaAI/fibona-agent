import io
import traceback
import types
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from typing import TextIO

from IPython.core.interactiveshell import InteractiveShell
from traitlets.config import Config

from core.utils import log_cell


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

    A cell that hangs or crashes the interpreter takes the agent down with it.
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
        # The host keeps cell history, so skip IPython's (and its sqlite file in ~/.ipython).
        config = Config()
        config.HistoryAccessor.enabled = False
        self.shell = InteractiveShell(config=config)
        # Show only what cells print; a trailing expression is not echoed as `Out[n]: ...`.
        self.shell.ast_node_interactivity = "none"
        # Tracebacks go to CellResult.error instead of being printed into the output.
        self.shell._showtraceback = lambda *args, **kwargs: None
        self.shell.set_custom_exc((Quit,), lambda *args, **kwargs: [])
        self.bound = set()  # Names the agent put in, as opposed to what cells defined.

    def bind(self, **names) -> None:
        """Put names (call_me, quit, ...) into the cells' global namespace, replacing old ones."""
        self.shell.user_ns.update(names)
        self.bound.update(names)

    @log_cell
    def execute(self, code: str) -> CellResult:
        """Return cell output and errors; Quit sets the quit flag, KeyboardInterrupt propagates."""
        buffer = io.StringIO()
        self.bind(input=self.read_input)
        stdout = Tee(buffer) if self.stdout is None else Tee(self.stdout, buffer)
        stderr = Tee(buffer) if self.stderr is None else Tee(self.stderr, buffer)
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = self.shell.run_cell(code, store_history=False)
        error = result.error_before_exec or result.error_in_exec
        if isinstance(error, KeyboardInterrupt):
            raise error  # Ctrl+C is the user stopping the agent, not a bug for it to repair.
        if isinstance(error, Quit):
            return CellResult(buffer.getvalue(), quit=True)
        return CellResult(buffer.getvalue(), "".join(traceback.format_exception(error)) if error else None)

    def variables(self) -> dict[str, str]:
        """What cells have defined: name -> type, with the length of containers."""
        found = {}
        for name, value in self.shell.user_ns.items():
            if name.startswith("_") or name in self.shell.user_ns_hidden or name in self.bound:
                continue
            if isinstance(value, types.ModuleType):
                continue
            kind = type(value).__name__
            try:
                kind += f"[{len(value)}]"
            except Exception:
                pass
            found[name] = kind
        return found


def _unavailable_input(prompt: str = "") -> str:
    raise RuntimeError("No input handler configured for this environment")
