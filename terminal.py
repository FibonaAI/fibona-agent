import argparse
import io
import os
import sys
from collections import deque
from pathlib import Path
from queue import Queue
from threading import Event, Thread
from time import monotonic
from uuid import uuid4

from dotenv import load_dotenv
from rich.syntax import Syntax
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Collapsible, Static, TextArea

from notebook import Notebook


class EventStream(io.TextIOBase):
    """Forward Python output to the UI without writing to its terminal."""

    def __init__(self, send, channel: str):
        self.send, self.channel = send, channel

    def write(self, text: str) -> int:
        if text:
            self.send(("output", self.channel, text))
        return len(text)

    def flush(self) -> None:
        pass


class Composer(TextArea):
    BINDINGS = (Binding("enter", "send", "Send", priority=True),)

    def on_focus(self) -> None:
        self.placeholder = ""

    def on_blur(self) -> None:
        self.placeholder = "Click to type"

    async def action_send(self) -> None:
        await self.app._submit(self.text)


class Terminal(App):
    LOGO = """███████╗██╗██████╗  ██████╗ ███╗   ██╗ █████╗
██╔════╝██║██╔══██╗██╔═══██╗████╗  ██║██╔══██╗
█████╗  ██║██████╔╝██║   ██║██╔██╗ ██║███████║
██╔══╝  ██║██╔══██╗██║   ██║██║╚██╗██║██╔══██║
██║     ██║██████╔╝╚██████╔╝██║ ╚████║██║  ██║
╚═╝     ╚═╝╚═════╝  ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═╝"""
    TITLE = "Fibona"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = (Binding("ctrl+c", "quit", "Quit", priority=True),)
    CSS = """
    Screen { background: #20202e; color: #d6d6dd; }
    #messages { height: 1fr; padding: 1 2; }
    #messages, #composer { scrollbar-size-vertical: 0; }
    #welcome { height: 1fr; content-align: center middle; color: #78628f; }
    .message { height: auto; margin-bottom: 1; padding-left: 3; }
    .user { background: #41414e; padding: 1 2; }
    .notice { color: #9494a4; }
    .error { color: #f18c8c; }
    Collapsible {
        background: transparent; border: none; padding: 0;
        margin-bottom: 1; height: auto;
    }
    CollapsibleTitle { width: 1fr; color: #ba94ed; }
    Collapsible > Contents { padding: 0 0 0 3; }
    #bottom { height: auto; padding: 0 2; }
    #input { height: auto; background: #41414e; padding: 1; }
    #prompt { width: 2; height: 1; color: #9494a4; }
    #composer {
        height: auto; min-height: 1; max-height: 40vh;
        border: none; background: #41414e; color: #d6d6dd; padding: 0;
    }
    #status-line { height: 1; }
    #status { width: 1fr; height: 1; }
    #speed { width: auto; height: 1; margin-left: 1; color: #e5c07b; }
    #keys { height: 1; color: #9494a4; }
    """

    def __init__(self, *, debug: bool = False):
        self.debug_logging = debug
        load_dotenv(".env")
        super().__init__()
        self.session_id = uuid4().hex
        self.cwd = Path.home() / ".fibona" / self.session_id / "workspace"
        self.cwd.mkdir(parents=True)
        self.notebook = Notebook()
        self.notebook_path = self.cwd.parent / "session.ipynb"
        self.thread = None
        self.inputs = Queue()
        self.closed = Event()
        self.session_state = "Ready"
        self.state_started = monotonic()
        self.model_requests = 0
        self.model_started = 0.0
        self.token_samples = deque()
        self.cells = {}
        self.output_widget = None
        self.output_text = ""
        self.output_channel = None
        self.model = os.getenv("FIBONA_MODEL", "gpt-6-astra")

    def _set_state(self, state: str) -> None:
        self.session_state = state
        self.state_started = monotonic()
        self._refresh_status()

    def _refresh_status(self) -> None:
        now = monotonic()
        state = self.session_state
        samples = self.token_samples
        while len(samples) > 1 and samples[1][0] <= now - 3:
            samples.popleft()
        speed = 0.0
        if len(samples) > 1:
            speed = (samples[-1][1] - samples[0][1]) / max(0.25, min(3, now - samples[0][0]))
        if self.model_requests:
            state = f"Thinking · {now - self.model_started:.1f}s"
            if samples[-1][0] > self.model_started:
                state = f"Generating · {now - self.model_started:.1f}s"
        elif state.startswith("Running"):
            state = f"{state} · {now - self.state_started:.1f}s"
        if self.model_requests or self.session_state.startswith("Running"):
            state = f"{'⠋⠙⠹⠸⠼⠴⠦⠧'[int(now * 10) % 8]} {state}"
        self.query_one("#status", Static).update(
            Text.assemble(
                (state, "#a4cea2"),
                (" · ", "#9494a4"),
                (self.model, "#ba94ed"),
                (" · ", "#9494a4"),
                Text("notebook", style="#f0a6ca underline").on(click="app.copy_notebook_path"),
                (" · ", "#9494a4"),
                (self.session_id, "#f0a6ca"),
            )
        )
        self.query_one("#speed", Static).update(f"↓ {speed:.0f} tok/s")

    async def _append(self, widget) -> None:
        messages = self.query_one("#messages", VerticalScroll)
        self.query_one("#welcome").display = False
        follow = messages.scroll_y >= messages.max_scroll_y - 1
        await messages.mount(widget)
        if follow:
            messages.scroll_end(animate=False, immediate=False)

    def _reset_output(self) -> None:
        self.output_widget = None
        self.output_text = ""
        self.output_channel = None

    async def _submit(self, text: str) -> None:
        if not text.strip():
            return
        if self.thread is not None and self.session_state != "Waiting for input":
            self.notify("Agent is running. Keep your draft until it asks for input.")
            return
        await self._append(Static(Text.assemble(("\u203a ", "#9494a4"), text), classes="message user"))
        self.query_one("#composer", Composer).load_text("")
        self._reset_output()
        if self.thread is not None:
            self.inputs.put(text)
            self._set_state("Running")
            return
        self.cells.clear()
        self.inputs, self.closed = Queue(), Event()
        self.thread = Thread(target=self._run_agent, args=(text,), daemon=True, name="fibona-agent")
        try:
            self.thread.start()
        except Exception as error:
            await self._finish(f"Could not start execution: {error}", failed=True)
        if self.thread is not None:
            self._set_state("Running")

    def _tick(self) -> None:
        if self.model_requests or self.session_state.startswith("Running") or len(self.token_samples) > 1:
            self._refresh_status()

    async def _handle_agent_event(self, event, closed: Event) -> None:
        if closed.is_set():
            return
        kind, *data = event
        if kind == "model_start":
            if not self.model_requests:
                self.model_started = data[0]
            if not self.token_samples:
                self.token_samples.append((data[0], 0.0))
            self.model_requests += 1
        elif kind == "model_delta":
            self.token_samples.append((data[0], self.token_samples[-1][1] + data[1]))
        elif kind == "model_end":
            self.model_requests -= 1
        elif kind == "cell":
            number, summary, code = data
            self.notebook.start_cell(number, summary, code)
            self._save_notebook()
            self._reset_output()
            error_widget = Static("", classes="error")
            card = Collapsible(
                Static(Syntax(code, "python", word_wrap=True)), error_widget, title=Text(f"● cell {number} · {summary}")
            )
            self.cells[number] = (card, summary, error_widget)
            await self._append(card)
            self._set_state(f"Running · cell {number}")
        elif kind == "output":
            channel, text = data
            self.notebook.output(channel, text)
            if self.output_widget is None or channel != self.output_channel:
                self._reset_output()
                self.output_channel = channel
                self.output_widget = Static(classes="message error" if channel == "stderr" else "message")
                await self._append(self.output_widget)
            self.output_text += text
            self.output_widget.update(Text(self.output_text.rstrip("\n")))
            self.output_widget.display = bool(self.output_text.strip())
            messages = self.query_one("#messages", VerticalScroll)
            if messages.scroll_y >= messages.max_scroll_y - 1:
                messages.scroll_end(animate=False)
        elif kind == "input":
            self._reset_output()
            if data[0].strip() not in {"", ">"}:
                self.notebook.output("stdout", data[0] + "\n")
                await self._append(Static(Text(data[0]), classes="message notice"))
            self._save_notebook()
            self._set_state("Waiting for input")
        elif kind == "result":
            number, error = data
            self.notebook.finish_cell(error)
            self._save_notebook()
            card, summary, error_widget = self.cells[number]
            card.title = Text(f"{'✗' if error else '✓'} cell {number} · {summary}")
            if error:
                error_widget.update(Text(error))
        elif kind == "error":
            await self._finish(data[0], failed=True)
        elif kind == "done":
            await self._finish("Session ended. Send a new task to start again.")

    async def _finish(self, message: str, *, failed: bool = False) -> None:
        self._save_notebook()
        self._close_session()
        self.thread = None
        self._reset_output()
        self._set_state("Error" if failed else "Ready")
        await self._append(Static(Text(message), classes="message error" if failed else "message notice"))

    def _save_notebook(self) -> None:
        try:
            self.notebook.save(self.notebook_path)
        except OSError as error:
            self.notify(f"Could not save notebook: {error}", severity="error", timeout=10)

    def _close_session(self) -> None:
        self.model_requests = 0
        self.closed.set()
        self.inputs.put(None)

    def _run_agent(self, task: str) -> None:
        """Run cells in a background thread with access to the live terminal app."""
        inputs, closed = self.inputs, self.closed

        def send(event):
            if not closed.is_set():
                self.call_later(self._handle_agent_event, event, closed)

        def check_closed():
            if closed.is_set():
                raise KeyboardInterrupt

        def create_response(**params):
            check_closed()
            send(("model_start", monotonic()))
            try:
                with create(**{**params, "stream": True}) as stream:
                    for event in stream:
                        check_closed()
                        if event.type in {
                            "response.output_text.delta",
                            "response.function_call_arguments.delta",
                            "response.reasoning_text.delta",
                            "response.reasoning_summary_text.delta",
                        }:
                            # ponytail: UTF-8 bytes / 4 estimates tokens; use provider token counts when available.
                            send(("model_delta", monotonic(), len(event.delta.encode("utf-8")) / 4))
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
                send(("model_end",))

        def read_input(prompt: str = "") -> str:
            check_closed()
            send(("input", str(prompt)))
            text = inputs.get()
            if text is None:
                raise KeyboardInterrupt
            return text

        def on_cell(number, summary, code):
            check_closed()
            send(("cell", number, summary, code))

        def on_result(number, result):
            send(("result", number, result.error))

        try:
            from openai import OpenAI

            from core.agent import INSTRUCTIONS, Agent, Mind
            from core.env import IPythonEnv

            env = IPythonEnv(
                stdout=EventStream(send, "stdout"), stderr=EventStream(send, "stderr"), read_input=read_input
            )
            env.bind(terminal=self)
            instructions = (
                INSTRUCTIONS
                + """
## Terminal
`terminal` is the live Textual App; cells run in a background thread.
Access UI through `terminal.call_from_thread(callback, *args, **kwargs)`; callbacks may be async and must not block.
"""
            )
            with OpenAI() as client:
                create = client.responses.create
                client.responses.create = create_response
                mind = Mind(client=client, model=self.model, instructions=instructions)
                Agent(mind, cwd=self.cwd).run(
                    task, env=env, on_cell=on_cell, on_result=on_result, debug=self.debug_logging
                )
            send(("done",))
        except KeyboardInterrupt:
            send(("error", "Execution interrupted."))
        except BaseException as error:
            send(("error", str(error) or type(error).__name__))

    def action_copy_notebook_path(self) -> None:
        self.copy_to_clipboard(str(self.notebook_path))
        self.notify("Notebook path copied.")

    def compose(self) -> ComposeResult:
        yield VerticalScroll(Static(Text(self.LOGO, no_wrap=True, overflow="crop"), id="welcome"), id="messages")
        with Vertical(id="bottom"):
            with Horizontal(id="input"):
                yield Static("\u203a", id="prompt")
                yield Composer(id="composer", soft_wrap=True)
            with Horizontal(id="status-line"):
                yield Static(id="status", markup=False)
                yield Static("↓ 0 tok/s", id="speed", markup=False)
            yield Static("Enter send · Ctrl+C quit", id="keys")

    def on_mount(self) -> None:
        self.query_one("#composer", Composer).focus()
        self._set_state("Ready")
        self.set_interval(0.05, self._tick)

    async def action_quit(self) -> None:
        self._close_session()
        self.exit()

    def on_unmount(self) -> None:
        self._close_session()
        self._save_notebook()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Fibona terminal UI.")
    parser.add_argument("--debug", action="store_true", help="Record cells and model calls in agent.log.")
    args = parser.parse_args()
    app = Terminal(debug=args.debug)
    stdout, stderr = sys.stdout, sys.stderr
    try:
        app.run()
    finally:
        app._close_session()
        if app.thread is not None:
            app.thread.join(timeout=0.3)
        sys.stdout, sys.stderr = stdout, stderr
    if app.thread is not None and app.thread.is_alive():
        # ponytail: threads cannot be killed; exit the CLI after restoring the terminal.
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(app.return_code)
    return 0


if __name__ == "__main__":
    sys.exit(main())
