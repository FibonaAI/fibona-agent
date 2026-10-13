"""Fibona on MicroPython: the cell loop, running on the board.

Two ways to start it, which differ only in how model requests travel:
- bridge mode: `make bridge TASK="..."` sends this file over the serial link; bridge.py defines ARGV and
  the limits from config.py, and forwards model requests (serial_request).
- standalone mode: firmware with this module frozen in, plus config.py and standalone.py (saved as main.py)
  on the board; it joins Wi-Fi and calls main() with http_transport(...).
"""

import gc
import io
import json
import os
import sys

try:
    from collections.abc import Callable  # For type checkers only; MicroPython never evaluates annotations.
except ImportError:
    pass

INSTRUCTIONS = """
You are the Fibona agent, running on an ESP32 board under MicroPython.

You act only by writing the next cell of a MicroPython program, which runs in a persistent session and calls you
back to write the one after.

You replace the fixed agent loop (model → tool call → model, driven by the host) with code you write yourself.
Each step is a cell; the cell calls you as a function whenever it needs a decision, and your answer is the next
cell. A cell that never calls you has no next cell. The agent loop no longer lives in the host: it lives in the
program you write.


## call_me

`call_me(input, **params)` wakes you and returns the Responses API result as a dict (`r["output_text"]` for your
reply, `r["output"]` for all items).
- `input`: a string, or a list of messages and items.
- Optional: `tools` and `tool_choice` for your own tools, `text` for structured output,
  `reasoning={"effort": "high"}` to think harder (never below medium).

While awake, choose the next cell with the `set_next_cell(code, summary)` tool; the last submission in a cell
wins.

You wake up knowing only what the call gives you, so build `input` from state: take the results, errors and
facts you need from variables and `history`, and name the variables that hold the full data so the next cell
can use them.


## State

- **Variables** persist across cells. Inspect them with `globals()`; values are not automatically sent to you.
- **`history`**: one `{cell, code, summary, output, error}` dict per recent cell (`cell` is its number).
  Each output keeps only its tail, and the oldest cells are dropped when free RAM runs low;
  save what you must keep.
- Variables live in RAM and are lost on reset. Files on the board's flash persist: write modules with
  `with open(...)` and import them. Imports are cached; `del sys.modules[name]` before importing a changed module.


## In the session

- `call_llm_api(input, **params) -> str`: a plain model call without your instructions or host tool.
- `quit()`: ends the run. Call it only when the task is done or the user asks.
- This is MicroPython, a subset of Python: no pip, no CPython packages, about 150 KB of free RAM.
  Hardware is available through `machine`. There is no user input; work from the task you were given.
- For compute-heavy functions, `@micropython.native` or `@micropython.viper` compiles them to machine code.
- Never print secrets or pass them to a model.


## Working principles

- Solve problems from first principles; persist through failures and verify results.
- Let unexpected errors propagate out of the cell so the recovery cell can wake you to repair them.
""".strip()

SET_NEXT_CELL = {
    "type": "function",
    "name": "set_next_cell",
    "strict": True,
    "description": "Submit the complete Python cell to run after the current cell finishes, with a short summary.",
    "parameters": {
        "type": "object",
        "properties": {"code": {"type": "string"}, "summary": {"type": "string", "maxLength": 256}},
        "required": ["code", "summary"],
        "additionalProperties": False,
    },
}

PROTECTED = ("model", "instructions", "tools", "reasoning", "stream", "background", "input", "parallel_tool_calls",
             "store", "previous_response_id")  # fmt: skip

# --- AGENT ---------------------------------------------------------------------------------------------


class Mind:
    """The agent's mind: instructions and the `set_next_cell` tool, reached through `request`.

    `request(body) -> dict` sends one Responses API request; it plays the OpenAI client's part.
    """

    def __init__(self, request: Callable[[dict], dict], instructions: str):
        self.request, self.instructions = request, instructions

    def wake(self, input=(), submit: Callable[[str, str], None] | None = None, **body) -> dict:
        """Wake the mind. Exposed to cells as `call_me`.

        input: a string or list of input items, passed positionally or by keyword; defaults to empty.
        body: other fields of the Responses API request body. Commonly used:
            tools: the caller's own tools; `set_next_cell` is always appended.
            tool_choice: "auto" / "required" / "none", or a specific tool.
            text: output format, e.g. {"format": {"type": "json_object"}}.
            max_output_tokens: cap on generated tokens, reasoning included.
            metadata, ...: passed through unchanged.
          Normalized or fixed by the mind:
            instructions: the mind itself.
            model: dropped here; the transport fills in the configured model.
            reasoning: only `effort` is kept, raised to at least "medium".
            include: always ["reasoning.encrypted_content"]; the caller's value is replaced.
            stream, background: dropped; the full response is returned.
            store, previous_response_id: stateless; to continue a conversation, put its
                earlier items (e.g. `r["output"]`) into `input`.
            parallel_tool_calls: always off, so `set_next_cell` never shares a turn
                with the caller's tools and the caller never has to answer it.
        submit: where `set_next_cell(code, summary)` lands. The agent binds a fresh one
            per cell, so a late call from a finished cell cannot choose the next one.
        Returns the response as a dict, with `output_text` added by the transport.

        Example (agent side, before each cell runs):
            slot = Slot()
            env.ns.update(call_me=lambda input=(), submit=slot.submit, **body: mind.wake(input, submit, **body))

        Example (cell side, written by the model; only `submit` is already bound):
            r = call_me(input="Sensor read failed:\\n" + log + "\\nFix it and submit the next cell.")
            print(r["output_text"])
        """
        effort = (body.get("reasoning") or {}).get("effort")
        tools = [t for t in body.get("tools", []) if t.get("name") != SET_NEXT_CELL["name"]]
        body = {k: v for k, v in body.items() if k not in PROTECTED}
        body.update(
            instructions=self.instructions,
            reasoning={"effort": effort if effort in ("medium", "high", "xhigh") else "medium"},
            tools=tools + [SET_NEXT_CELL],
            parallel_tool_calls=False,
            store=False,
            # Without server-side storage, reasoning can only be replayed in its encrypted form.
            include=["reasoning.encrypted_content"],
        )
        items = _input_items(input)
        while True:
            response = self.request(dict(body, input=items))
            host = None
            for c in response["output"]:
                if c["type"] == "function_call" and c["name"] == SET_NEXT_CELL["name"]:
                    host = c
                    break
            if host is None:
                return response  # A plain reply, or one of the caller's tools for it to answer.
            output = {
                "type": "function_call_output",
                "call_id": host["call_id"],
                "output": _run_host_tool(host, submit),
            }
            # Replay every output item, including encrypted reasoning, without server-side storage.
            items = items + [{k: v for k, v in o.items() if v is not None} for o in response["output"]] + [output]

    def query(self, input, **params) -> str:
        """A plain model call used as a function (summarize, extract, classify, ...).

        No predefined agent instructions or host tool are added, so it cannot schedule a cell.
        Exposed to cells as `call_llm_api`; returns the reply text. stream, background and
        previous_response_id are dropped, and the request is never stored.

        Example (cell side):
            summary = call_llm_api("Summarize in one line:\\n" + log)
        """
        params = {k: v for k, v in params.items() if k not in ("stream", "background", "previous_response_id")}
        return self.request(dict(params, store=False, input=_input_items(input)))["output_text"]


def _input_items(value) -> list:
    return [{"role": "user", "content": value}] if isinstance(value, str) else list(value)


def _run_host_tool(call: dict, submit: Callable[[str, str], None]) -> str:
    try:
        args = json.loads(call["arguments"])
        if not args.get("code", "").strip():
            raise ValueError("code must be a non-empty string")
        submit(args["code"], args["summary"])
        result = {"scheduled": True}
    except Exception as error:
        result = {"scheduled": False, "error": str(error)}
    return json.dumps(result)


class Memory:
    """The host's execution records; cells receive copies. Trimmed to keep RAM for the next request."""

    def __init__(self, max_output: int, min_free: int):
        self.max_output = max_output  # Characters of each cell's output kept in history (the tail).
        self.min_free = min_free  # Bytes of RAM to keep free for the next model request.
        self.history = []  # One {cell, code, summary, output, error} dict per recent cell.
        self.cells = 0  # Number of finished cells, which is also the number of the running one.

    def record(self, code: str, summary: str, result: "CellResult") -> None:
        """After a cell: keep its record, trimmed to leave RAM for the next model request.

        output: only its last max_output characters are kept, behind a line saying how many were cut.
        history: the oldest records are dropped until at least min_free bytes of RAM are free again;
            the newest record always stays.
        Each record carries its cell number, so numbers stay stable as older records go.
        """
        output = result.output
        if len(output) > self.max_output:
            output = "[%d earlier characters cut]\n" % (len(output) - self.max_output) + output[-self.max_output :]
        self.history.append({"cell": self.cells, "code": code, "summary": summary, "output": output,
                             "error": result.error})  # fmt: skip
        self.cells += 1
        gc.collect()
        while len(self.history) > 1 and gc.mem_free() < self.min_free:
            self.history.pop(0)
            gc.collect()

    def snapshot(self) -> list[dict]:
        """Copies for cells to read as history; editing them cannot alter the originals."""
        return [dict(record) for record in self.history]


class Slot:
    """The next cell chosen during the current one. Closed once that cell ends, so a late
    `set_next_cell` from it fails instead of overwriting a newer cell's choice."""

    def __init__(self):
        self.code: str | None = None
        self.summary: str | None = None
        self.closed = False

    def submit(self, code: str, summary: str) -> None:
        if self.closed:
            raise RuntimeError("This cell has finished; its call_me can no longer choose a cell")
        self.code, self.summary = code, summary  # The last submission wins.


def _wake_cell(message: str) -> str:
    """A cell that only wakes the mind with `message`; the mind chooses the next cell."""
    return "response = call_me(input=%r)\nprint(response['output_text'])" % message


# --- ENV -----------------------------------------------------------------------------------------------


class Quit(BaseException):
    """Raised by `quit()` in a cell to end the run; cells' `except Exception` cannot swallow it."""


class CellResult:
    def __init__(self, output: str, error: str | None = None, quit: bool = False):
        self.output = output  # Everything the cell wrote to the terminal.
        self.error = error  # Traceback if the cell failed.
        self.quit = quit  # The cell raised Quit.


class Capture(io.IOBase):
    """A copy of all terminal output (print, help(), sys.stdout), attached with os.dupterm."""

    def __init__(self):
        self.chunks = []

    def write(self, data: bytes) -> int:
        self.chunks.append(bytes(data))
        return len(data)

    def readinto(self, buf: bytearray) -> None:
        return None  # Never supplies input.

    def text(self) -> str:
        return b"".join(self.chunks).decode().replace("\r\n", "\n")


class MicroPythonEnv:
    """A persistent namespace: runs cells and holds their variables (imports and functions too) across cells.

    Output still reaches the terminal and is also captured. A cell that hangs or crashes the board takes the
    agent down with it.
    """

    def __init__(self):
        self.ns = {}

    def execute(self, code: str) -> CellResult:
        """Return cell output and errors; Quit sets the quit flag, KeyboardInterrupt propagates.

        output: everything written to the terminal while the cell runs, captured through os.dupterm,
            so print, help() and sys.stdout writes all count. Model requests detach the capture.
        error: the traceback of an exception the cell let escape, or None.
        Side effects of a failed cell stay in the namespace; nothing is rolled back.
        """
        capture = Capture()
        os.dupterm(capture)  # MicroPython cannot redirect sys.stdout; dupterm copies all terminal output.
        try:
            exec(code, self.ns)
        except Quit:
            return CellResult(capture.text(), quit=True)
        except Exception as e:
            buf = io.StringIO()
            sys.print_exception(e, buf)
            return CellResult(capture.text(), buf.getvalue())
        finally:
            os.dupterm(None)
        return CellResult(capture.text())


# --- TRANSPORT -----------------------------------------------------------------------------------------


def serial_request(body: dict) -> dict:
    """Bridge mode: send one model request over the serial link and wait for its response.

    The request goes out as a single line `@@model <json body>`. bridge.py on the computer adds the
    model and API key, calls the Responses API, and writes the result back as one JSON line on stdin.
    The running cell's capture is detached while printing, so the request is not recorded as output.
    Raises OSError with the error bridge.py reports: the HTTP status and body, or a network error.
    """
    capture = os.dupterm(None)  # Keep the request out of the cell's output.
    print("@@model " + json.dumps(body))
    os.dupterm(capture)
    response = json.loads(sys.stdin.readline())
    if response.get("error"):
        raise OSError(response["error"])
    return _with_output_text(response)


def http_transport(base_url: str, api_key: str, model: str) -> Callable[[dict], dict]:
    """Standalone mode: return a request function that calls the Responses API over Wi-Fi.

    base_url: the API root, e.g. "https://api.openai.com/v1"; requests go to base_url + "/responses".
    api_key: sent as a Bearer token.
    model: used when the body names none, which is always the case for Mind.wake.
    The returned function raises OSError with the HTTP status and body on any non-200 response.
    Wi-Fi must already be connected; standalone.py does that first.

    Example (standalone.py):
        request = http_transport(config.OPENAI_BASE_URL, config.OPENAI_API_KEY, config.FIBONA_MODEL)
        main([config.TASK], request, config.MAX_OUTPUT, config.MIN_FREE)
    """
    import requests

    def http_request(body: dict) -> dict:
        body.setdefault("model", model)
        r = requests.post(base_url + "/responses", headers={"Authorization": "Bearer " + api_key}, json=body)
        try:
            if r.status_code != 200:
                raise OSError({"status": r.status_code, "body": r.text})
            return _with_output_text(r.json())
        finally:
            r.close()

    return http_request


def _with_output_text(response: dict) -> dict:
    response["output_text"] = "".join(
        c["text"] for o in response["output"] if o["type"] == "message" for c in o["content"]
        if c["type"] == "output_text"
    )  # fmt: skip
    return response


# Bootstrap cell: working state and context helpers.
# They live in the session, not the host, so later cells can replace them.
BOOTSTRAP = r'''
instructions = """
Your working instructions (`instructions`), variables() and build_context() are Python state in this session;
you can replace them in later cells.
Use call_me(input=build_context() + [{"role": "user", "content": observations}]) to send these instructions and
your selected context.
Keep useful data and task-specific helpers in session variables; update var_docs to describe them.
startup_task preserves the initial task. Maintain task_state before each call_me():
update goal and constraints when the task is steered, and completed and pending as work progresses.
Record verified outcomes in completed; planned work belongs in pending.
build_context() carries all fields on every call.
History summaries record intent; use cell output and errors to determine what actually happened.
""".strip()

startup_task = " ".join(argv)
task_state = {"goal": startup_task, "constraints": [], "completed": [], "pending": []}
RECENT = 5  # Cells shown in full; fewer than on a computer to save RAM.
var_docs = {
    "startup_task": "Original task; preserve when the current goal changes.",
    "task_state": "Current goal, constraints, verified completed work and pending work; update before call_me().",
}


def variables():
    """Select names and types from the live session; cells may replace this policy."""
    found = {}
    for name, value in globals().items():
        if name.startswith("_") or name in ("call_me", "call_llm_api", "quit", "history", "argv"):
            continue
        kind = type(value).__name__
        if kind == "module":
            continue
        try:
            kind += "[%d]" % len(value)
        except Exception:
            pass
        found[name] = kind
    return found


def build_context():
    """Select context from session variables and the recent history; cells may replace this."""
    described = [
        name + ": " + kind + (" (%s)" % var_docs[name] if var_docs.get(name) else "")
        for name, kind in variables().items()
    ]
    lines = [
        "Startup task: " + startup_task,
        "Current goal: %s" % task_state["goal"],
        "Constraints: %r" % (task_state["constraints"],),
        "Completed: %r" % (task_state["completed"],),
        "Pending: %r" % (task_state["pending"],),
        "Current task cell: %d" % (history[-1]["cell"] + 1 if history else 0),
        "Variables: " + (", ".join(described) or "none"),
    ]
    start = max(0, len(history) - RECENT)
    for cell in history[:start]:
        result = "failed: " + cell["error"].strip().split("\n")[-1] if cell["error"] else "ok"
        lines.append("cell %d: %s [%s]" % (cell["cell"], cell["summary"], result))
    blocks = []
    for i in range(start, len(history)):
        cell = history[i]
        parts = ["### cell %d · %s" % (cell["cell"], cell["summary"]), "code:", cell["code"].strip()]
        output = cell["output"].strip()
        if output:
            if len(output) > 500:
                output = output[:250] + "\n[Full output: history[%d]['output']]\n" % i + output[-250:]
            parts += ["output:", output]
        if cell["error"]:
            parts += ["error:", cell["error"].strip()]
        blocks.append("\n".join(parts))
    context = "\n".join(lines + ["Recent task cells:", "\n\n".join(blocks) or "none yet"])
    return [{"role": "developer", "content": instructions}, {"role": "user", "content": context}]


call_me(input=build_context() + [{"role": "user", "content": "Start the task and submit the next cell."}])
'''.strip()


def main(argv: list[str], request: Callable[[dict], dict], max_output: int, min_free: int) -> None:
    """Run the task in argv on the board until the agent quits or stops.

    Failed cells trigger a repair cell; if that also fails, the run stops.
    quit() ends the run. A cell that hangs or crashes the board takes the agent down with it.

    argv: the task; the bootstrap cell joins it with spaces into startup_task.
    request: how model requests travel: serial_request (bridge mode) or http_transport(...) (standalone).
    max_output, min_free: history limits from config.py; see Memory.record.
    """
    env = MicroPythonEnv()
    env.ns["argv"] = argv
    mind = Mind(request, INSTRUCTIONS)
    memory = Memory(max_output, min_free)
    repair_message = "The previous cell failed (its error is in the history). Repair it and continue the program."
    repair_summary = "Repair the failed cell."

    def quit_() -> None:
        raise Quit

    code, summary = BOOTSTRAP, "Run the bootstrap cell."
    while True:
        slot = Slot()
        # Restore runtime bindings each cell; mutations to shared objects still persist.
        env.ns.update(
            call_me=lambda input=(), submit=slot.submit, **body: mind.wake(input, submit, **body),
            call_llm_api=mind.query,
            quit=quit_,
            history=memory.snapshot(),
        )
        print("\n[cell %d] %s" % (memory.cells, summary))
        result = env.execute(code)
        slot.closed = True
        if not result.quit and result.error is None and slot.code is None:
            result.error = "The cell finished without choosing a next cell via set_next_cell."
        if result.quit:
            return
        memory.record(code, summary, result)

        if result.error is None:
            code, summary = slot.code, slot.summary
            continue
        print(result.error)
        if summary == repair_summary:
            print("The repair cell failed, so the model is unreachable; stopping.")
            return
        # Side effects of the failed cell are not rolled back; the mind repairs from here.
        message = (
            repair_message + "\n\n" + json.dumps({"bootstrap_cell": BOOTSTRAP, "code": code, "error": result.error})
        )
        code, summary = _wake_cell(message), repair_summary


if __name__ == "__main__":  # Bridge mode: this file arrives over the serial link, after bridge.py's definitions.
    main(ARGV, serial_request, MAX_OUTPUT, MIN_FREE)  # noqa: F821 -- bridge.py defines these.
