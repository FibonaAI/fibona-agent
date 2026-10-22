"""A cell-owned terminal UI; it never starts or schedules the core agent."""

import asyncio
import atexit
import inspect
import io
import os
import sys
import traceback
from collections import deque
from concurrent.futures import wait
from pathlib import Path
from queue import Queue
from threading import Event, Thread, current_thread
from time import monotonic
from uuid import uuid4

from IPython.core.interactiveshell import InteractiveShell
from prompt_toolkit.application import Application, run_in_terminal
from prompt_toolkit.formatted_text import FormattedText, PygmentsTokens, to_formatted_text
from prompt_toolkit.input import create_input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output import ColorDepth, create_output
from prompt_toolkit.renderer import print_formatted_text
from prompt_toolkit.styles import Style, merge_styles
from prompt_toolkit.styles.pygments import style_from_pygments_cls
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import TextArea
from pygments.lexers import PythonLexer
from pygments.styles.monokai import MonokaiStyle

from bootstraps.terminal.notebook import Notebook
from core.env import CellResult, Quit

TERMINAL_INSTRUCTIONS = """
`terminal` is a live Python UI object created by the bootstrap cell. Core runs in the caller's thread; the UI runs in its own thread.
Use terminal.call_from_thread(callback, *args) to change live widgets or app.layout on the UI thread; it returns a Future.
The UI implementation is in source_code['bootstraps/terminal/terminal.py']. You can redefine it in a cell, not just edit its file.
To replace the UI, call terminal.close(), define your replacement, then start it with get_ipython().
The terminal bootstrap uses IPython pre_run_cell/post_run_cell events and standard Python streams for its I/O and cell records.
input() receives text from the composer and print() writes to its message area. Closing a UI does not end the agent.
Use print() in the chosen cell for user-facing dialogue, including questions before input().
Do not print call_me response text: it is returned to the calling cell, and the chosen cell delivers the reply.
Do not repeat a greeting or question across a model reply and the next cell's output.
""".strip()


class EventStream(io.TextIOBase):
    """Forward a cell's live output while core still captures its complete result."""

    def __init__(self, terminal, channel, upstream):
        self.terminal, self.channel, self.upstream = terminal, channel, upstream

    def write(self, text):
        if text:
            # Keep the host's captured output and application streams; the UI owns terminal display.
            for stream in getattr(self.upstream, "streams", (self.upstream,)):
                if not stream.isatty():
                    stream.write(text)
            self.terminal.notebook.output(self.channel, text)
            self.terminal.call_from_thread(self.terminal._append_output, text, self.channel)
        return len(text)

    def flush(self):
        for stream in getattr(self.upstream, "streams", (self.upstream,)):
            if not stream.isatty():
                stream.flush()


class Terminal:
    """Attach to the existing IPython session; the cell owns this UI and its thread.

    The conversation is printed above the composer into the terminal's own scrollback, so scrolling,
    selection and copying are native; the application itself only draws the composer and status bar.
    """

    instructions = TERMINAL_INSTRUCTIONS
    LOGO = """███████╗██╗██████╗  ██████╗ ███╗   ██╗ █████╗
██╔════╝██║██╔══██╗██╔═══██╗████╗  ██║██╔══██╗
█████╗  ██║██████╔╝██║   ██║██╔██╗ ██║███████║
██╔══╝  ██║██╔══██╗██║   ██║██║╚██╗██║██╔══██║
██║     ██║██████╔╝╚██████╔╝██║ ╚████║██║  ██║
╚═╝     ╚═╝╚═════╝  ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═╝"""
    INDENT = "   "
    FLUSH_DELAY = 0.03  # Seconds to batch live output before printing it above the composer.
    CODE_PREVIEW = 8  # Source lines shown in a code card; Ctrl+O shows all lines of later cells.
    CODE_WIDTH = 96  # Widest code card, in terminal cells.

    def __init__(self, shell: InteractiveShell, *, input=None, output=None):
        self.shell = shell
        self.session_id = uuid4().hex
        self.notebook = Notebook()
        self.notebook_path = Path.home() / ".fibona" / self.session_id / "session.ipynb"
        self.notebook_path.parent.mkdir(parents=True)
        self.inputs = Queue()
        self.ready, self.closed = Event(), Event()
        self.ui_error = None
        self.user_quit = False
        self.status = "Ready"
        self.started = monotonic()
        self.model_requests = 0
        self.token_samples = deque()
        self.thread = None
        self.previous_io = None
        self.cell_slot = None
        self.completed_cell = None  # (number, result, slot) of the last cell IPython finished, until core confirms it.
        # A replacement UI continues the previous one's pending cell summary.
        previous = shell.user_ns.get("terminal")
        current = getattr(previous, "current_cell", None)
        self.next_summary = current[1] if current else "Initialize the terminal."
        self.current_cell = None
        self.cell_buffer = None
        self.attached = False
        self.previous_excepthook = shell.sys_excepthook if sys.excepthook == shell.excepthook else sys.excepthook
        # call_llm_api is the bound Mind.query; recover the Mind to show model progress. None outside a core session.
        self.mind = getattr(shell.user_ns.get("call_llm_api"), "__self__", None)
        self.model = self.mind.model if self.mind is not None else "Python"
        self.create_response = self.mind.client.responses.create if self.mind is not None else None
        self.show_welcome = not shell.user_ns.get("history", [])
        self.show_code = False  # Show each cell's full source instead of a preview; toggled with Ctrl+O.
        self.cells = {}
        self.pending = {"text": "", "channel": "stdout"}  # Live output not yet printed: at most a partial line.
        self.flush_handle = None
        self.composer = TextArea(
            multiline=True,
            height=self._composer_height,
            prompt=[("class:notice", "\u203a ")],
            style="class:composer",
        )
        self.keys = KeyBindings()

        @self.keys.add("enter")
        def submit(event):
            text = self.composer.text
            if text.strip() and self.status == "Waiting for input":
                self._append_user(text)
                self.composer.text = ""
                self._set_state("Running")
                self.inputs.put(text)

        @self.keys.add("escape", "enter")
        def newline(event):
            self.composer.buffer.insert_text("\n")

        @self.keys.add("c-o")
        def toggle_code(event):
            self.show_code = not self.show_code
            self.app.invalidate()

        @self.keys.add("c-c")
        def quit_ui(event):
            self.user_quit = True
            self.app.exit()

        self.app = self.create_application(
            input=input if input is not None else create_input(stdin=sys.__stdin__),
            output=output if output is not None else create_output(stdout=sys.__stderr__),
        )

    def create_application(self, *, input, output):
        """Build the editable widget tree; later cells may replace this method or the whole UI."""
        status = VSplit(
            [
                Window(FormattedTextControl(self._status_text), height=1, style="class:status"),
                Window(
                    FormattedTextControl(self._speed_text),
                    width=lambda: len(self._speed_text()),
                    height=1,
                    style="class:speed",
                ),
            ],
            height=1,
        )
        keys = Window(FormattedTextControl(self._keys_text), height=1, style="class:notice")
        return Application(
            layout=Layout(
                VSplit(
                    [
                        Window(width=2),
                        HSplit(
                            [
                                Window(height=1),
                                HSplit(
                                    [
                                        Window(height=1),
                                        VSplit([Window(width=1), self.composer, Window(width=1)]),
                                        Window(height=1),
                                    ],
                                    style="class:composer",
                                ),
                                VSplit([Window(width=1), HSplit([status, keys]), Window(width=1)], height=2),
                            ]
                        ),
                        Window(width=2),
                    ],
                ),
                focused_element=self.composer,
            ),
            key_bindings=self.keys,
            full_screen=False,
            erase_when_done=True,  # A closed or replaced UI leaves only the conversation in the scrollback.
            color_depth=ColorDepth.TRUE_COLOR,
            include_default_pygments_style=False,
            # Foreground accents only: the terminal's own background and text colors show through.
            style=merge_styles(
                [
                    style_from_pygments_cls(MonokaiStyle),
                    Style.from_dict(
                        {
                            "composer": "bg:#41414e #d6d6dd",
                            "status": "#a4cea2",
                            "model": "#ba94ed",
                            "notebook": "#f0a6ca underline",
                            "session": "#f0a6ca",
                            "logo": "#78628f",
                            "notice": "#9494a4",
                            "user": "bg:#41414e #d6d6dd",
                            "cell": "#ba94ed bold",
                            "error": "#f18c8c",
                            "ok": "#a4cea2",
                            "frame": "#5c5c70",
                            "code": "bg:#272822 #f8f8f2",
                            "speed": "#e5c07b",
                        }
                    ),
                ]
            ),
            input=input,
            output=output,
            refresh_interval=0.1,
            after_render=lambda app: self.ready.set(),
        )

    def _composer_height(self):
        size = self.app.output.get_size()
        width = max(1, size.columns - 8)
        lines = sum(max(1, (get_cwidth(line) + width - 1) // width) for line in self.composer.text.split("\n"))
        return min(lines, max(1, int(size.rows * 0.4) - 2))

    def _keys_text(self):
        width = self.app.output.get_size().columns - 6
        code = "short code" if self.show_code else "full code"
        full = f"Enter send · Alt+Enter newline · Ctrl+O {code} · Ctrl+C quit"
        if len(full) <= width:
            return full
        short = "Enter send · Ctrl+C quit"
        return short if len(short) <= width else "Enter · Ctrl+C"

    def _speed_text(self):
        now = monotonic()
        while len(self.token_samples) > 1 and self.token_samples[1][0] < now - 3:
            self.token_samples.popleft()
        speed = 0.0
        if len(self.token_samples) > 1:
            first, last = self.token_samples[0], self.token_samples[-1]
            speed = (last[1] - first[1]) / max(0.25, last[0] - first[0])
        return f" ↓ {speed:.0f} tok/s"

    def _status_text(self):
        state = f"Thinking · {monotonic() - self.started:.1f}s" if self.model_requests else self.status
        if self.model_requests and self.token_samples and self.token_samples[-1][0] > self.started:
            state = f"Generating · {monotonic() - self.started:.1f}s"
        parts = [("", state)]
        budget = max(1, self.app.output.get_size().columns - 6 - len(self._speed_text()))
        for part in [
            ("class:model", " · " + self.model),
            ("class:notebook", " · notebook"),
            ("class:session", " · " + self.session_id[:8]),
        ]:
            visible_width = sum(len(item[1]) for item in parts if "[ZeroWidthEscape]" not in item[0])
            if visible_width + len(part[1]) <= budget:
                if part[0] == "class:notebook":
                    # An OSC 8 hyperlink: terminals that support it open the notebook on click.
                    parts.extend(
                        [
                            ("class:session", " · "),
                            ("[ZeroWidthEscape]", f"\x1b]8;;{self.notebook_path.as_uri()}\x1b\\"),
                            ("class:notebook", "notebook"),
                            ("[ZeroWidthEscape]", "\x1b]8;;\x1b\\"),
                        ]
                    )
                else:
                    parts.append(part)
        return FormattedText(parts)

    def _set_state(self, state):
        self.status, self.started = state, monotonic()
        self.app.invalidate()

    def _print(self, fragments):
        """Print formatted text above the composer, into the terminal's scrollback; returns a Future."""

        def write():
            print_formatted_text(
                self.app.output, FormattedText(fragments), self.app.style, color_depth=self.app.color_depth
            )
            self.app.output.flush()

        return run_in_terminal(write)

    def _flush_output(self, final=True):
        """Print buffered live output; unless final, keep a trailing partial line for the next write."""
        self.flush_handle = None
        text = self.pending["text"]
        cut = len(text) if final else text.rfind("\n") + 1
        if not cut:
            return None
        self.pending["text"] = text[cut:]
        lines = text[:cut].removesuffix("\n").split("\n")
        style = "class:error" if self.pending["channel"] == "stderr" else ""
        fragments = []
        for line in lines:
            fragments += [("", self.INDENT), (style, line), ("", "\n")]
        return self._print(fragments)

    def _append_text(self, text):
        self._append_output(text)

    def _append_output(self, text, channel="stdout"):
        if self.pending["channel"] != channel:
            self._flush_output()
            self.pending["channel"] = channel
        self.pending["text"] += text
        if self.flush_handle is None:
            self.flush_handle = self.app.loop.call_later(self.FLUSH_DELAY, self._flush_output, False)

    def _print_welcome(self):
        size = self.app.output.get_size()
        logo = self.LOGO if size.columns - 4 >= max(map(len, self.LOGO.splitlines())) else "FIBONA"
        lines = [line for part in logo.splitlines() for line in (("", "  "), ("class:logo", part), ("", "\n"))]
        return self._print([("", "\n"), *lines, ("", "\n  "), ("class:notice", "Enter a task to begin."), ("", "\n")])

    def _append_user(self, text):
        self._flush_output()
        width = max(1, self.app.output.get_size().columns - 4)
        fragments = [("", "\n")]
        for number, line in enumerate(text.split("\n")):
            line = ("\u203a " if number == 0 else "  ") + line
            fragments += [
                ("", "  "),
                ("class:user", " " + line + " " * max(0, width - get_cwidth(line) - 1)),
                ("", "\n"),
            ]
        self._print(fragments)

    def _code_card(self, code):
        """The cell's highlighted source in a frame: a preview, or all of it with show_code."""
        columns = self.app.output.get_size().columns
        width = max(8, min(self.CODE_WIDTH, columns - len(self.INDENT) - 6))  # Inside "│ " and " │".
        lines = [[]]
        for style, text in to_formatted_text(PygmentsTokens(list(PythonLexer().get_tokens(code.expandtabs(4))))):
            for index, part in enumerate(text.split("\n")):
                if index:
                    lines.append([])
                if part:
                    lines[-1].append((style, part))
        while lines and not lines[-1]:
            lines.pop()
        shown = lines if self.show_code else lines[: self.CODE_PREVIEW]
        if hidden := len(lines) - len(shown):
            shown.append([("class:notice", f"… {hidden} more lines in the notebook")])
        rule = "─" * (width + 2)
        fragments = [("", self.INDENT), ("class:frame", f"┌{rule}┐"), ("", "\n")]
        for line in shown:
            fitted = [(f"class:code {style}", text) for style, text in _fit(line, width)]
            fragments += [
                ("", self.INDENT),
                ("class:frame", "│"),
                ("class:code", " "),
                *fitted,
                ("class:code", " "),
                ("class:frame", "│"),
                ("", "\n"),
            ]
        return [*fragments, ("", self.INDENT), ("class:frame", f"└{rule}┘"), ("", "\n")]

    def _add_cell(self, number, summary, code, live=True):
        self._flush_output()
        self.cells[number] = {
            "summary": summary,
            "code": code,
            "state": "●",
            "started": monotonic() if live else None,
            "ended": None,
        }
        self._print([("", "\n  "), ("class:cell", f"● cell {number} · {summary}"), ("", "\n"), *self._code_card(code)])

    def _finish_cell(self, number, result, final=True):
        """Print the cell's error and its ✓/✗ line once; a cell that succeeded waits for core to confirm it."""
        cell = self.cells[number]
        if cell["state"] != "●":
            return
        self._flush_output()
        if cell["ended"] is None:
            cell["ended"] = monotonic()
        if result.error and "\n" in result.error.strip():  # A one-line error is shown in the ✗ line alone.
            self.pending["channel"] = "stderr"
            self.pending["text"] += result.error.rstrip("\n") + "\n"
            self._flush_output()
        if not final:
            return
        cell["state"] = "✗" if result.error else "✓"
        parts = [f"{cell['state']} cell {number}"]
        if cell["started"] is not None:
            parts.append(f"{cell['ended'] - cell['started']:.1f}s")
        if result.error:
            parts.append(result.error.strip().splitlines()[-1])
        self._print([("", "  "), ("class:error" if result.error else "class:ok", " · ".join(parts)), ("", "\n")])
        self.app.invalidate()

    def call_from_thread(self, callback, *args):
        """Run a UI callback on this app's thread and return a Future for its result."""
        if self.closed.is_set():
            raise RuntimeError("Terminal UI is closed")

        async def invoke():
            value = callback(*args)
            return await value if inspect.isawaitable(value) else value

        return asyncio.run_coroutine_threadsafe(invoke(), self.app.loop)

    def start(self):
        """Start this UI and attach its I/O and records to IPython's existing cell events."""
        self.thread = Thread(target=self._run_ui, name="fibona-ui", daemon=True)
        self.thread.start()
        self.ready.wait()
        if self.ui_error is not None:
            raise self.ui_error
        if self.closed.is_set():
            raise RuntimeError("Terminal UI ended before startup completed")
        if self.create_response is not None:
            self.mind.client.responses.create = self._create_response
        namespace = self.shell.user_ns
        instructions = namespace.get("instructions", "")
        if TERMINAL_INSTRUCTIONS not in instructions:
            namespace["instructions"] = "\n\n".join(part for part in (instructions, TERMINAL_INSTRUCTIONS) if part)
        namespace.setdefault("source_code", {})["bootstraps/terminal/terminal.py"] = Path(__file__).read_text()
        namespace.setdefault("var_docs", {})["terminal"] = (
            "Cell-owned UI; close and replace it without restarting core."
        )
        if self.show_welcome:
            self.call_from_thread(self._print_welcome)
        for number, cell in enumerate(namespace.get("history", [])):
            self.notebook.start_cell(number, cell["summary"], cell["code"])
            self.notebook.finish_cell(cell["error"], output=cell["output"])
            self.call_from_thread(self._add_cell, number, cell["summary"], cell["code"], False)
            if cell["output"]:
                self.call_from_thread(self._append_output, cell["output"])
            self.call_from_thread(self._finish_cell, number, CellResult(cell["output"], cell["error"]))
        self.shell.events.register("pre_run_cell", self._begin_cell)
        self.shell.events.register("post_run_cell", self._end_cell)
        self.attached = True
        atexit.register(self.close)
        result = self.shell.displayhook.exec_result
        if result is not None:
            self._begin_cell(result.info)
        return self

    def _run_ui(self):
        try:
            self.app.run(handle_sigint=False, set_exception_handler=False)
        except BaseException as error:
            self.ui_error = error
        finally:
            self.closed.set()
            self.ready.set()
            self.inputs.put(None)
            if self.user_quit:
                # The terminal bootstrap owns terminal keyboard exit, including cells blocked in native I/O or executors.
                # Application.run has restored the TTY before this point; core and the launcher stay synchronous.
                sys.__stdout__.flush()
                sys.__stderr__.flush()
                os._exit(130)

    def close(self):
        """Detach this bootstrap's IPython hooks and I/O, leaving the session's variables intact."""
        if self.completed_cell is not None:
            _, result, slot = self.completed_cell
            if slot is not None and slot.closed:
                error = result.error
                if error is None and slot.code is None:
                    error = "The cell finished without choosing a next cell via set_next_cell."
                self._confirm_cell(CellResult(result.output, error))
        self._restore_cell_io()
        restore_after_cell = (
            sys.excepthook == self.shell.excepthook and self.shell.sys_excepthook == self._uncaught_error
        )
        if restore_after_cell:
            self.shell.sys_excepthook = self.previous_excepthook
        if self.attached:
            self.shell.events.unregister("pre_run_cell", self._begin_cell)
            if not restore_after_cell:
                self.shell.events.unregister("post_run_cell", self._end_cell)
            self.attached = False
            atexit.unregister(self.close)
        if sys.excepthook == self._uncaught_error:
            sys.excepthook = self.previous_excepthook
        if self.create_response is not None and self.mind.client.responses.create == self._create_response:
            self.mind.client.responses.create = self.create_response
        if not self.closed.is_set():
            flushed = self.call_from_thread(self._flush_output)  # Print the remaining output before the UI exits.
            if self.thread is not current_thread():
                wait([flushed], timeout=5)
            self.call_from_thread(self.app.exit)
        if self.thread is not None and self.thread is not current_thread():
            self.thread.join()
        self.closed.set()
        self.inputs.put(None)
        self.notebook.save(self.notebook_path)

    def _uncaught_error(self, kind, error, tb):
        self.close()
        self.previous_excepthook(kind, error, tb)

    def _read_input(self, prompt=""):
        if self.closed.is_set():
            raise self.ui_error or KeyboardInterrupt
        if prompt:
            self.call_from_thread(self._append_text, str(prompt) + "\n")
        self.call_from_thread(self._flush_output)  # Show a partial line, such as a question printed with end="".
        self.call_from_thread(self._set_state, "Waiting for input").result()
        text = self.inputs.get()
        if text is None:  # The UI closed while waiting.
            raise self.ui_error or KeyboardInterrupt
        return text

    def _begin_cell(self, info):
        """Use IPython's cell source and the existing model submission callback for UI metadata."""
        namespace = self.shell.user_ns
        number = len(namespace.get("history", []))
        if self.completed_cell is not None and number:
            _, _, slot = self.completed_cell
            if slot is not None:
                self.next_summary = slot.summary
            previous = namespace["history"][-1]
            self._confirm_cell(CellResult(previous["output"], previous["error"]))
        if number and namespace["history"][-1]["error"]:
            self.next_summary = "Repair the failed cell."
        self.current_cell = (number, self.next_summary, info.raw_cell)
        self._record_cell(*self.current_cell)
        self.previous_io = (sys.stdout, sys.stderr, namespace["input"])
        # Core's capture buffer, if any: it already holds output printed before this hook ran.
        self.cell_buffer = next(
            (stream for stream in reversed(getattr(sys.stdout, "streams", ())) if isinstance(stream, io.StringIO)),
            None,
        )
        if self.cell_buffer is not None and self.cell_buffer.getvalue():
            prefix = self.cell_buffer.getvalue()
            self.notebook.output("stdout", prefix)
            self.call_from_thread(self._append_output, prefix)
        self.cell_slot = namespace["call_me"].keywords["submit"].__self__  # The Slot core bound for this cell.
        namespace["input"] = self._read_input
        sys.stdout, sys.stderr = EventStream(self, "stdout", sys.stdout), EventStream(self, "stderr", sys.stderr)

    def _restore_cell_io(self):
        if self.previous_io is not None:
            stdout, stderr, read_input = self.previous_io
            if isinstance(sys.stdout, EventStream) and sys.stdout.terminal is self:
                sys.stdout = stdout
            if isinstance(sys.stderr, EventStream) and sys.stderr.terminal is self:
                sys.stderr = stderr
            if self.shell.user_ns.get("input") == self._read_input:
                self.shell.user_ns["input"] = read_input
            self.previous_io = None
        self.cell_slot = None

    def _record_cell(self, number, summary, code):
        self.notebook.start_cell(number, summary, code)
        self.notebook.save(self.notebook_path)
        self.call_from_thread(self._add_cell, number, summary, code)
        self.call_from_thread(self._set_state, f"Running · cell {number}")

    def _confirm_cell(self, result):
        """Confirm the previous cell after core has closed its submission slot."""
        number, recorded, _ = self.completed_cell
        self.completed_cell = None
        if result.output != recorded.output or result.error != recorded.error:
            cell = self.notebook.cells[-1]
            cell["outputs"] = [item for item in cell["outputs"] if item["output_type"] != "error"]
            self.notebook.finish_cell(result.error, output=result.output)
            self.notebook.save(self.notebook_path)
        if not self.closed.is_set():
            self.call_from_thread(self._finish_cell, number, result)

    def _end_cell(self, execution):
        """Record IPython's result and close this bootstrap on quit or interruption."""
        if not self.attached:
            # IPython restores its saved hook after code returns; finish a close requested inside that code.
            if sys.excepthook == self._uncaught_error:
                sys.excepthook = self.previous_excepthook
            self.shell.events.unregister("post_run_cell", self._end_cell)
            return
        slot = self.cell_slot
        self._restore_cell_io()
        if execution is None:
            self.close()
            return
        error = execution.error_before_exec or execution.error_in_exec
        stopping = isinstance(error, (Quit, KeyboardInterrupt))
        output = self.cell_buffer.getvalue() if self.cell_buffer is not None else None
        result = CellResult(
            output or "",
            "".join(traceback.format_exception(error)) if error is not None and not stopping else None,
            quit=stopping,
        )
        number = self.current_cell[0]
        if not stopping:
            # Submissions remain valid until core returns from IPython and closes the slot.
            self.completed_cell = (number, result, slot)
        self.notebook.finish_cell(result.error, output=output)
        self.notebook.save(self.notebook_path)
        # A failed or stopping cell is final; a successful one is confirmed when core has closed its slot.
        self.call_from_thread(self._finish_cell, number, result, stopping or result.error is not None)
        self.call_from_thread(self._set_state, "Ended" if result.quit else "Ready")
        if stopping:
            self.close()
        else:
            # Install after IPython has restored the hook it temporarily replaces while running code.
            sys.excepthook = self._uncaught_error

    def _model_event(self, kind, delta=0):
        if kind == "start":
            self.model_requests += 1
            self.started = monotonic()
            if not self.token_samples:
                self.token_samples.append((self.started, 0))
        elif kind == "delta":
            self.token_samples.append((monotonic(), self.token_samples[-1][1] + delta))
        else:
            self.model_requests -= 1
        self.app.invalidate()

    def _create_response(self, **params):
        """Show streamed progress while preserving the model's complete response and host tool calls."""
        self.call_from_thread(self._model_event, "start")
        try:
            with self.create_response(**{**params, "stream": True}) as stream:
                for event in stream:
                    if event.type in {
                        "response.output_text.delta",
                        "response.function_call_arguments.delta",
                        "response.reasoning_text.delta",
                        "response.reasoning_summary_text.delta",
                    }:
                        # Rough token estimate: ~4 bytes per token.
                        self.call_from_thread(self._model_event, "delta", len(event.delta.encode()) / 4)
                    elif event.type == "response.failed":
                        raise RuntimeError(
                            event.response.error.message if event.response.error else "Model request failed."
                        )
                    elif event.type in {"response.completed", "response.incomplete"}:
                        return event.response
                    elif event.type == "error":
                        raise RuntimeError(event.message)
            raise RuntimeError("Model stream ended without a final response.")
        finally:
            self.call_from_thread(self._model_event, "end")


def _fit(fragments, width):
    """Clip a line of fragments to `width` terminal cells, marking a cut with an ellipsis, and pad the rest."""
    total = sum(get_cwidth(text) for _, text in fragments)
    limit = width if total <= width else width - 1
    clipped, used = [], 0
    for style, text in fragments:
        kept = ""
        for char in text:
            if used + get_cwidth(char) > limit:
                break
            kept += char
            used += get_cwidth(char)
        if kept:
            clipped.append((style, kept))
        if len(kept) < len(text):
            break
    if total > width:
        clipped.append(("class:frame", "…"))
        used += 1
    return [*clipped, ("", " " * (width - used))]
