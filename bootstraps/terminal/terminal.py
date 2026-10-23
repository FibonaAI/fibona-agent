"""A cell-owned terminal UI; it never starts or schedules the core agent."""

import asyncio
import atexit
import base64
import inspect
import io
import os
import signal
import subprocess
import sys
import traceback
from collections import deque
from functools import partial
from pathlib import Path
from queue import Queue
from threading import Event, Thread, current_thread, main_thread
from time import monotonic
from uuid import uuid4

from IPython.core.interactiveshell import InteractiveShell
from prompt_toolkit.application import Application
from prompt_toolkit.clipboard import ClipboardData
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import FormattedText, fragment_list_to_text
from prompt_toolkit.input import create_input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, ScrollablePane, VSplit, Window, WindowAlign
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.screen import Char
from prompt_toolkit.lexers import PygmentsLexer
from prompt_toolkit.mouse_events import MouseButton, MouseEventType
from prompt_toolkit.output import ColorDepth, create_output
from prompt_toolkit.styles import Style, merge_styles
from prompt_toolkit.styles.pygments import style_from_pygments_cls
from prompt_toolkit.utils import get_cwidth
from prompt_toolkit.widgets import TextArea
from pygments.lexers import PythonLexer
from pygments.styles.monokai import MonokaiStyle

from bootstraps.terminal.notebook import Notebook
from core.env import STOPPED, CellResult, Quit, Stop

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


class SelectableBody(HSplit):
    """Paint mouse-selected text after the conversation's source and output windows."""

    def __init__(self, terminal, children):
        super().__init__(children)
        self.terminal = terminal

    def write_to_screen(self, screen, mouse_handlers, write_position, parent_style, erase_bg, z_index):
        # ScrollablePane's default cells must not override a child's card background.
        super().write_to_screen(screen, mouse_handlers, write_position, "", erase_bg, z_index)
        screen.draw_with_z_index(1, partial(self.terminal._paint_body, screen))


class Terminal:
    """Attach to the existing IPython session; the cell owns this UI and its thread."""

    instructions = TERMINAL_INSTRUCTIONS
    LOGO = """███████╗██╗██████╗  ██████╗ ███╗   ██╗ █████╗
██╔════╝██║██╔══██╗██╔═══██╗████╗  ██║██╔══██╗
█████╗  ██║██████╔╝██║   ██║██╔██╗ ██║███████║
██╔══╝  ██║██╔══██╗██║   ██║██║╚██╗██║██╔══██║
██║     ██║██████╔╝╚██████╔╝██║ ╚████║██║  ██║
╚═╝     ╚═╝╚═════╝  ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═╝"""

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
        self.cells = {}
        self.output_block = None
        self.follow_output = True
        self.selection_anchor = self.selection_cursor = self.drag_start = None
        self.messages = HSplit([], padding=1)
        self.timeline = ScrollablePane(
            self.messages, show_scrollbar=False, keep_cursor_visible=False, keep_focused_window_visible=False
        )
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

        @self.keys.add("c-c")
        def quit_ui(event):
            if self.selection_anchor != self.selection_cursor:
                self._copy_text(self._selected_text())
                self.selection_anchor = self.selection_cursor = None
                self.app.invalidate()
                return
            if self.status.startswith("Running"):
                # Core's stop: the running cell ends and the run waits for the user. Pressed again while
                # "Stopping", for a cell stuck in native code, Ctrl+C quits.
                signal.pthread_kill(main_thread().ident, signal.SIGUSR1)
                self._set_state("Stopping")
                return
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
        welcome = Window(FormattedTextControl(self._welcome_text), align=WindowAlign.CENTER)
        body = SelectableBody(
            self,
            [
                ConditionalContainer(welcome, filter=Condition(lambda: self.show_welcome)),
                ConditionalContainer(self.timeline, filter=Condition(lambda: not self.show_welcome)),
            ],
        )
        return Application(
            layout=Layout(
                VSplit(
                    [
                        Window(width=2),
                        HSplit(
                            [
                                Window(height=1),
                                body,
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
                    style="class:background",
                ),
                focused_element=self.composer,
            ),
            key_bindings=self.keys,
            full_screen=True,
            mouse_support=True,
            color_depth=ColorDepth.TRUE_COLOR,
            include_default_pygments_style=False,
            style=merge_styles(
                [
                    style_from_pygments_cls(MonokaiStyle),
                    Style.from_dict(
                        {
                            "": "bg:#20202e #d6d6dd",
                            "background": "bg:#20202e #d6d6dd",
                            "messages": "bg:#20202e #d6d6dd",
                            "composer": "bg:#41414e #d6d6dd",
                            "status": "#a4cea2",
                            "model": "#ba94ed",
                            "notebook": "#f0a6ca underline",
                            "session": "#f0a6ca",
                            "logo": "#78628f",
                            "notice": "#9494a4",
                            "user": "bg:#41414e #d6d6dd",
                            "cell": "#ba94ed",
                            "error": "#f18c8c",
                            "speed": "#e5c07b",
                            "selection": "bg:#695a80 #ffffff",
                        }
                    ),
                ]
            ),
            input=input,
            output=output,
            refresh_interval=0.1,
            before_render=self._follow_scroll,
            after_render=self._after_render,
        )

    def _composer_height(self):
        size = self.app.output.get_size()
        width = max(1, size.columns - 8)
        lines = sum(max(1, (get_cwidth(line) + width - 1) // width) for line in self.composer.text.split("\n"))
        return min(lines, max(1, int(size.rows * 0.4) - 2))

    def _keys_text(self):
        width = self.app.output.get_size().columns - 6
        full = "Enter send · Alt+Enter newline · Drag to copy · Ctrl+C stop/quit"
        if len(full) <= width:
            return full
        short = "Enter send · Ctrl+C stop/quit"
        return short if len(short) <= width else "Enter · Ctrl+C"

    def _body_height(self):
        return max(1, self.app.output.get_size().rows - self._composer_height() - 6)

    def _follow_scroll(self, app):
        if not self.show_welcome:
            width = max(1, app.output.get_size().columns - 4)
            height = self.messages.preferred_height(width, sys.maxsize).preferred
            self.timeline.max_available_height = max(height, self._body_height())
            if self.follow_output:
                self.timeline.vertical_scroll = max(0, height - self._body_height())

    def _after_render(self, app):
        # Keep clicks, wheel scrolling and text selection available while the composer has focus.
        handlers = app.renderer.mouse_handlers.mouse_handlers
        wrapped = {}
        for y in range(app.output.get_size().rows):
            for x in range(app.output.get_size().columns):
                original = handlers[y][x]
                if original not in wrapped:

                    def handle(event, original=original):
                        return self._handle_pointer(event, original)

                    wrapped[original] = handle
                handlers[y][x] = wrapped[original]
        self.ready.set()

    def _handle_pointer(self, event, original):
        """Distinguish a title click from a drag, including releases outside the conversation."""
        size = self.app.output.get_size()
        inside = 2 <= event.position.x < size.columns - 2 and 1 <= event.position.y <= self._body_height()
        if not inside and self.drag_start is None:
            # FormattedTextControl counts OSC 8 escapes in fragment clicks; use the rendered link span.
            char = self.app.renderer.last_rendered_screen.data_buffer[event.position.y][event.position.x]
            if event.event_type == MouseEventType.MOUSE_UP and "class:notebook" in char.style.split():
                return self._open_notebook(event)
            return original(event)
        point = (
            min(self._body_height(), max(1, event.position.y)),
            min(size.columns - 2, max(2, event.position.x)),
        )
        if event.event_type in {MouseEventType.SCROLL_UP, MouseEventType.SCROLL_DOWN}:
            self.selection_anchor = self.selection_cursor = self.drag_start = None
            width = max(1, size.columns - 4)
            height = self.messages.preferred_height(width, self.timeline.max_available_height).preferred
            maximum = max(0, height - self._body_height())
            step = -3 if event.event_type == MouseEventType.SCROLL_UP else 3
            self.timeline.vertical_scroll = min(maximum, max(0, self.timeline.vertical_scroll + step))
            self.follow_output = self.timeline.vertical_scroll == maximum
        elif event.event_type == MouseEventType.MOUSE_DOWN and event.button == MouseButton.LEFT:
            self.selection_anchor = self.selection_cursor = self.drag_start = point
        elif event.event_type == MouseEventType.MOUSE_MOVE and self.drag_start is not None:
            self.selection_cursor = point
            self.follow_output = False
        elif event.event_type == MouseEventType.MOUSE_UP and self.drag_start is not None:
            self.selection_cursor = point
            self.drag_start = None
            if self.selection_anchor != self.selection_cursor:
                self._copy_text(self._selected_text())
            else:
                self.selection_anchor = self.selection_cursor = None
                original(event)
        else:
            return original(event)
        self.app.invalidate()
        return None

    def _selection_ranges(self):
        if self.selection_anchor is None or self.selection_cursor is None:
            return
        start, end = sorted((self.selection_anchor, self.selection_cursor))
        for y in range(start[0], end[0] + 1):
            left = start[1] if y == start[0] else 2
            right = end[1] if y == end[0] else self.app.output.get_size().columns - 2
            yield y, left, right

    def _paint_body(self, screen):
        for y in range(1, 1 + self._body_height()):
            for x in range(2, self.app.output.get_size().columns - 2):
                char = screen.data_buffer[y][x]
                if char.style.strip() in {"", "[transparent]"}:
                    screen.data_buffer[y][x] = Char(char.char, "class:background")
        for y, left, right in self._selection_ranges():
            for x in range(left, right):
                char = screen.data_buffer[y][x]
                screen.data_buffer[y][x] = Char(char.char, char.style + " class:selection")

    def _selected_text(self):
        screen = self.app.renderer.last_rendered_screen
        start, end = sorted((self.selection_anchor, self.selection_cursor))
        for window, position in screen.visible_windows_to_write_positions.items():
            info = window.render_info
            if info is None or not all(
                position.ypos <= y < position.ypos + position.height
                and position.xpos <= x <= position.xpos + position.width
                for y, x in (start, end)
            ):
                continue
            # Copy logical lines within one source/output window, excluding UI padding and soft wraps.
            lines = (
                window.content.buffer.document.lines
                if isinstance(window.content, BufferControl)
                else [fragment_list_to_text(info.ui_content.get_line(row)) for row in range(info.ui_content.line_count)]
            )
            endpoints = []
            for y, x in (start, end):
                row, column = info.visible_line_to_row_col.get(y - position.ypos, (0, 0))
                line = lines[row]
                offset, width = x - position.xpos, 0
                while column < len(line) and width + get_cwidth(line[column]) <= offset:
                    width += get_cwidth(line[column])
                    column += 1
                endpoints.append((row, column))
            (first_row, first_column), (last_row, last_column) = endpoints
            lines = lines[first_row : last_row + 1]
            lines[-1] = lines[-1][:last_column]
            lines[0] = lines[0][first_column:]
            return "\n".join(lines)
        return "\n".join(
            "".join(screen.data_buffer[y][x].char for x in range(left, right)).rstrip()
            for y, left, right in self._selection_ranges()
        )

    def _copy_text(self, text):
        """Copy selected text to the app, terminal clipboard and macOS pasteboard."""
        if text:
            self.app.clipboard.set_data(ClipboardData(text))
            encoded = base64.b64encode(text.encode()).decode()
            self.app.output.write_raw(f"\x1b]52;c;{encoded}\x07")
            self.app.output.flush()
            if sys.platform == "darwin":
                subprocess.run(
                    ["/usr/bin/pbcopy"], input=text.encode(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )

    def _welcome_text(self):
        size = self.app.output.get_size()
        logo = (
            self.LOGO
            if size.columns - 4 >= max(map(len, self.LOGO.splitlines())) and self._body_height() >= 8
            else "FIBONA"
        )
        padding = max(0, (self._body_height() - len(logo.splitlines()) - 2) // 2)
        return FormattedText([("class:logo", "\n" * padding + logo), ("class:notice", "\n\nEnter a task to begin.")])

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
            ("class:notebook", " · notebook", self._open_notebook),
            ("class:session", " · " + self.session_id[:8]),
        ]:
            visible_width = sum(len(item[1]) for item in parts if "[ZeroWidthEscape]" not in item[0])
            if visible_width + len(part[1]) <= budget:
                if part[0] == "class:notebook":
                    parts.extend(
                        [
                            ("class:session", " · "),
                            ("[ZeroWidthEscape]", f"\x1b]8;;{self.notebook_path.as_uri()}\x1b\\"),
                            ("class:notebook", "notebook", self._open_notebook),
                            ("[ZeroWidthEscape]", "\x1b]8;;\x1b\\"),
                        ]
                    )
                else:
                    parts.append(part)
        return FormattedText(parts)

    def _open_notebook(self, event):
        if event.event_type == MouseEventType.MOUSE_UP:
            if sys.platform == "win32":
                os.startfile(self.notebook_path)
            else:
                opener = "open" if sys.platform == "darwin" else "xdg-open"
                subprocess.Popen(
                    [opener, str(self.notebook_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )

    def _set_state(self, state):
        self.status, self.started = state, monotonic()
        self.app.invalidate()

    def _append_text(self, text):
        self._append_output(text)

    def _text_window(self, text, style=""):
        return Window(FormattedTextControl(text), wrap_lines=True, dont_extend_height=True, style=style)

    def _append_user(self, text):
        self.show_welcome = False
        self.follow_output = True
        self.selection_anchor = self.selection_cursor = self.drag_start = None
        self.output_block = None
        self.messages.children.append(
            HSplit(
                [
                    Window(height=1),
                    VSplit([Window(width=1), self._text_window("\u203a " + text), Window(width=1)]),
                    Window(height=1),
                ],
                style="class:user",
            )
        )
        self.app.invalidate()

    def _append_output(self, text, channel="stdout"):
        self.show_welcome = False
        if self.output_block is None or self.output_block["channel"] != channel:
            block = {"text": "", "channel": channel}
            self.output_block = block
            window = self._text_window(lambda: block["text"].rstrip("\n"), "class:error" if channel == "stderr" else "")
            self.messages.children.append(
                ConditionalContainer(
                    VSplit([Window(width=3), window]), filter=Condition(lambda: bool(block["text"].strip()))
                )
            )
        self.output_block["text"] += text
        self.app.invalidate()

    def _add_cell(self, number, summary, code):
        self.output_block = None
        cell = {"summary": summary, "code": code, "collapsed": True, "state": "●"}
        self.cells[number] = cell
        header = self._text_window(
            lambda: FormattedText(
                [
                    (
                        "class:error" if cell["state"] == "✗" else "class:cell",
                        f"{'▶' if cell['collapsed'] else '▼'} {cell['state']} cell {number} · {cell['summary']}",
                        partial(self._toggle_cell, number),
                    )
                ]
            )
        )
        source = TextArea(text=code, read_only=True, lexer=PygmentsLexer(PythonLexer), dont_extend_height=True)
        self.messages.children.append(
            HSplit(
                [
                    header,
                    ConditionalContainer(
                        VSplit([Window(width=3), source]), filter=Condition(lambda: not cell["collapsed"])
                    ),
                ]
            )
        )
        self.app.invalidate()

    def _toggle_cell(self, number, event):
        if event.event_type == MouseEventType.MOUSE_UP:
            self.cells[number]["collapsed"] = not self.cells[number]["collapsed"]
            self.follow_output = False
            self.app.layout.focus(self.composer)
            self.app.invalidate()

    def _finish_cell(self, number, result):
        self.cells[number]["state"] = "■" if result.stopped else "✗" if result.error else "✓"
        if result.error:
            self._append_output(result.error + "\n", "stderr")
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
        for number, cell in enumerate(namespace.get("history", [])):
            self.notebook.start_cell(number, cell["summary"], cell["code"])
            self.notebook.finish_cell(cell["error"], output=cell["output"])
            self.call_from_thread(self._add_cell, number, cell["summary"], cell["code"])
            if cell["output"]:
                self.call_from_thread(self._append_output, cell["output"])
            self.call_from_thread(self._finish_cell, number, _recorded_result(cell))
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
        self.call_from_thread(self._set_state, "Waiting for input").result()
        text = self.inputs.get()
        if text is None:  # The UI closed while waiting.
            raise self.ui_error or KeyboardInterrupt
        return text

    def _begin_cell(self, info):
        """Use IPython's cell source and the existing model submission callback for UI metadata."""
        namespace = self.shell.user_ns
        history = namespace.get("history", [])
        number = len(history)
        if self.completed_cell is not None and number:
            completed, _, slot = self.completed_cell
            if slot is not None:
                self.next_summary = slot.summary
            # By number: a stop can leave cells after it that never ran, so history[-1] may not be this cell.
            self._confirm_cell(_recorded_result(history[completed]))
        # Cells core stopped before they ran never reached IPython's events; show them from history.
        for skipped in range(self.current_cell[0] + 1 if self.current_cell else number, number):
            cell = history[skipped]
            self.notebook.start_cell(skipped, cell["summary"], cell["code"])
            self.notebook.finish_cell(cell["error"], output=cell["output"])
            self.call_from_thread(self._add_cell, skipped, cell["summary"], cell["code"])
            self.call_from_thread(self._finish_cell, skipped, _recorded_result(cell))
        if number and history[-1]["error"]:
            stopped = history[-1]["error"] == STOPPED
            self.next_summary = "Wait for the user after a stop." if stopped else "Repair the failed cell."
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
        if isinstance(error, Stop):  # The user's stop: record it as core does, and the run goes on.
            result = CellResult(output or "", STOPPED, stopped=True)
        else:
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
        self.call_from_thread(self._finish_cell, number, result)
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
            self.model_requests = max(0, self.model_requests - 1)  # A stop can end a request before its start.
        self.app.invalidate()

    def _create_response(self, **params):
        """Show streamed progress while preserving the model's complete response and host tool calls."""
        try:
            self.call_from_thread(self._model_event, "start")
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


def _recorded_result(cell):
    """The CellResult of a history record."""
    return CellResult(cell["output"], cell["error"], stopped=cell["error"] == STOPPED)
