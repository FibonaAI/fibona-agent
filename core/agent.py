import json
from collections.abc import Callable
from contextlib import chdir
from functools import partial
from pathlib import Path
from threading import Lock

from openai import OpenAI
from openai.types.responses import Response, ResponseFunctionToolCall

from core.env import CellResult, IPythonEnv, Quit

INSTRUCTIONS = """
You are the Fibona agent.
Your mind is these instructions, the model and the `set_next_cell` tool, loaded only by `call_me`. Your memory is this session's variables and `history`. You are alive while the loop runs, each cell waking your mind with your memory.
You act only by writing the next cell of a Python program, which runs in a persistent session and wakes you when it is time to write the one after.
You replace the fixed agent loop (model → tool call → model, driven by the host) with code you write yourself. Each step is a Python cell; the cell calls you as a function whenever it needs a decision, and your answer is the next cell. A cell that never calls you has no next cell. The loop no longer lives in the host: it lives in the program you write.
## call_me
`call_me(input, **params)` wakes you and returns the Response (`r.output_text` for your reply, `r.output` for all items).
- `input`: a string, or a list of messages and items.
- Optional: `tools` and `tool_choice` for your own tools, `text` for structured output (e.g. `{"format": {"type": "json_object"}}`), `reasoning={"effort": "high"}` to think harder (never below medium), `max_output_tokens`.
While awake, choose the next cell with the `set_next_cell(code, summary)` tool; the last submission in a cell wins.
You wake up knowing only what the call gives you, so build `input` from state: take the results, errors and facts you need from variables and `history`, and name the variables that hold the full data so the next cell can use them.
## What a cell does
    pages = fetch_all(urls)  # do the work in code
    var_docs["pages"] = "url -> text, fetched live"  # describe the variable for future cells
    print(f"Fetched {len(pages)} pages.")  # the user sees only what you print
    r = call_me(input=f"`pages` (url -> text), {len(pages)} items; failed: {failed}. Next: summarize.")
    print(r.output_text)  # hand over; point to variables, don't paste data
## State
- **Variables** persist. Each time you wake you see their names, types, and descriptions from `var_docs`. Set or update `var_docs["name"] = "description"` when creating or changing a variable. Keep anything a later step needs in a well-named variable; prefix scratch values with `_` to hide them. Remove its description when deleting a variable.
- **`history`**: one `{code, summary, output, error}` dict per finished cell. Each time you wake you see the last ten in full, and a one-line summary and result of each older one; read an older cell from `history[i]` in a cell. A summary is what you planned when you chose the cell; the result says what happened.
  - **output** (`print`) is for the user; only its first and last 500 characters are kept. Print progress and findings for them; keep data for yourself in variables and point to them by name.
## In the session
- `source_code`: your runtime's source code, as a filename -> source text dict (`core/agent.py`, `core/env.py`). When asked how you are implemented, inspect it and pass relevant excerpts to `call_me` before answering.
- `call_llm(input, **params) -> str`: a plain model call with no instructions and no host tool. It is not you, just a way to use intelligence as a function; it cannot choose a cell, so it is safe to call from many threads at once.
- `input(prompt)`: asks the user and waits for the reply; this is how you wait for the user. When a task is done, ask what's next instead of stopping.
- `quit()`: ends the run. Call it only when the user asks.
- There is no pip: install with `uv pip install --python sys.executable <pkg>`.
- Never print secrets or pass them to a model.
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

# Request fields the mind fixes; callers of `call_me` cannot override them.
PROTECTED = {
    "model",
    "instructions",
    "tools",
    "reasoning",
    "stream",
    "background",
    "input",
    "parallel_tool_calls",
    "store",
    "previous_response_id",
}

Submit = Callable[[str, str], None]


class Mind:
    """The agent's mind: instructions, model and the `set_next_cell` tool.

    Loaded only by `call_me` (`wake`). Memory lives in the session's variables
    and `history`. The instructions define the agent's "self-awareness":
    the living cell loop is "me", each cell waking this mind with my memory
    to decide the next cell and continue that same loop.
    """

    def __init__(self, client: OpenAI, model: str, instructions: str):
        self.client, self.model, self.instructions = client, model, instructions

    def wake(self, *, submit: Submit, context: Callable[[], str], **body) -> Response:
        """Wake the mind. Exposed to cells as `call_me`.

        body: keyword arguments of `client.responses.create`. Commonly used:
            input: a string, or a list of input items (messages, function_call_output, ...).
            tools: the caller's own tools; `set_next_cell` is always appended.
            tool_choice: "auto" / "required" / "none", or a specific tool.
            text: output format, e.g. {"format": {"type": "json_object"}}.
            max_output_tokens: cap on generated tokens, reasoning included.
            include: extra output data; "reasoning.encrypted_content" is always added.
            metadata, ...: passed through unchanged.
          Fixed by the mind (caller values are ignored):
            model, instructions: the mind itself.
            reasoning: only `effort` is kept, raised to at least "medium".
            stream, background: always off; the full Response is returned.
            store, previous_response_id: stateless; to continue a conversation, put its
                earlier items (e.g. `r.output`) into `input`.
            parallel_tool_calls: always off, so `set_next_cell` never shares a turn
                with the caller's tools and the caller never has to answer it.
        submit: where `set_next_cell(code, summary)` lands. The agent binds a fresh one
            per cell, so a late call from a finished cell cannot choose the next one.
        context: what the mind sees of its memory, sent first as a developer message.

        Example (agent side, before each cell runs):
            slot = Slot()
            env.bind(call_me=partial(mind.wake, submit=slot.submit, context=lambda: "..."))

        Example (cell side, written by the model; `submit` and `context` are already bound):
            r = call_me(input="Tests failed:\\n" + log + "\\nFix them and submit the next cell.")
            print(r.output_text)
        """
        effort = (body.get("reasoning") or {}).get("effort")
        items = [{"role": "developer", "content": context()}, *_input_items(body.get("input", []))]
        tools = [t for t in body.get("tools", []) if t.get("name") != SET_NEXT_CELL["name"]]
        body = {k: v for k, v in body.items() if k not in PROTECTED}
        # TODO: filter PROTECTED fields from extra_body before passing it to the SDK.
        body.update(
            model=self.model,
            instructions=self.instructions,
            reasoning={"effort": effort if effort in {"medium", "high", "xhigh"} else "medium"},
            tools=[*tools, SET_NEXT_CELL],
            parallel_tool_calls=False,
            store=False,
            # Without server-side storage, reasoning can only be replayed in its encrypted form.
            include=list({*body.get("include", []), "reasoning.encrypted_content"}),
        )
        while True:
            response = self.client.responses.create(**body, input=items)
            host = next(
                (c for c in response.output if c.type == "function_call" and c.name == SET_NEXT_CELL["name"]), None
            )
            if host is None:
                return response  # A plain reply, or one of the caller's tools for it to answer.
            output = {"type": "function_call_output", "call_id": host.call_id, "output": _run_host_tool(host, submit)}
            # Replay every output item, including encrypted reasoning, without server-side storage.
            items = [*items, *(item.model_dump(mode="json", exclude_none=True) for item in response.output), output]

    def query(self, input, **params) -> str:
        """A plain model call used as a function (summarize, extract, classify, ...).

        No instructions and no host tool, so it is not the mind, can never choose a cell and is
        safe to call concurrently. Exposed to cells as `call_llm`; returns the reply text.

        Example (cell side):
            with ThreadPoolExecutor(8) as pool:
                summaries = list(pool.map(lambda src: call_llm("Summarize in one line:\\n" + src), sources))
        """
        params = {k: v for k, v in params.items() if k not in {"stream", "background", "previous_response_id"}}
        params.setdefault("model", self.model)
        return self.client.responses.create(**{**params, "store": False}, input=_input_items(input)).output_text


def _input_items(value) -> list:
    return [{"role": "user", "content": value}] if isinstance(value, str) else list(value)


def _run_host_tool(call: ResponseFunctionToolCall, submit: Submit) -> str:
    try:
        args = json.loads(call.arguments)
        if not args.get("code", "").strip():
            raise ValueError("code must be a non-empty string")
        submit(args["code"], args["summary"])
        result = {"scheduled": True}
    except Exception as error:
        result = {"scheduled": False, "error": str(error)}
    return json.dumps(result)


class Memory:
    """The agent's memory across cells: task, execution history and variable descriptions.

    Values live in the persistent Python session. `context` combines their
    names and types with these records, so each wake can see what has happened
    and continue the same task.
    """

    RECENT = 10  # Finished cells shown in full on each wake; older ones stay in `history`.

    def __init__(self, task: str):
        self.task = task
        self.history = []  # One {code, summary, output, error} dict per finished cell.
        self.var_docs = {}  # Variable name -> description, maintained by cells.

    @property
    def cells(self) -> int:
        """Number of finished cells, which is also the number of the running one."""
        return len(self.history)

    def record(self, code: str, summary: str, result: CellResult) -> None:
        """After a cell: keep its record, with the output clipped."""
        self.history.append({"code": code, "summary": summary, "output": _clip(result.output), "error": result.error})

    def context(self, variables: dict[str, str]) -> str:
        """The developer message for each wake: task, cell number, variables, finished cells.

        Example (cell 14; `report` has no description in `var_docs`):
            Memory (written by the agent's runtime; not the user's words, not yours):
            - The user's task, verbatim: Summarize the Hacker News front page
            - Current cell: 14
            - Variables defined by earlier cells: pages: dict[30] (url -> html, fetched live), failed: list[2], fetch_all: function, report: str
            - Older cells (summary and result; read `history[i]` in a cell for the rest):
            cell 0: Start the task. [ok]
            cell 1: Fetch the front page [failed: HTTPError: HTTP Error 403: Forbidden]
            cell 2: Fetch with a browser User-Agent [ok]
            cell 3: Fetch every linked article [ok]
            - Recent cells (the last 10, in full):
            ### cell 4 · Check which fetches failed
            code:
            ...

            ### cell 12 · Summarize each page in one line
            code:
            summaries = [call_llm("One line:\n" + html) for html in pages.values()]
            output:
            Summarized 30 pages.

            ### cell 13 · Write the report
            code:
            open("report.md", "w").write(render(summaries))
            error:
            Traceback (most recent call last):
            ...
            NameError: name 'render' is not defined
        """
        described = [self._describe(name, kind) for name, kind in variables.items()]
        lines = [
            "Memory (written by the agent's runtime; not the user's words, not yours):",
            f"- The user's task, verbatim: {self.task}",
            f"- Working directory: {Path.cwd()}. Save generated files here using relative paths unless the user specifies another location.",
            f"- Current cell: {self.cells}",
            "- Variables defined by earlier cells: " + (", ".join(described[:200]) or "none"),
        ]
        if older := self._older_cells():
            lines += ["- Older cells (summary and result; read `history[i]` in a cell for the rest):", older]
        return "\n".join([*lines, f"- Recent cells (the last {self.RECENT}, in full):", self._recent_cells()])

    def snapshot(self) -> list[dict]:
        """A copy of the history for cells to read as `history`; they cannot change the real one."""
        return [record.copy() for record in self.history]

    def _describe(self, name: str, kind: str) -> str:
        description = self.var_docs.get(name)
        return f"{name}: {kind}" + (f" ({description})" if description else "")

    def _older_cells(self) -> str:
        """One line per cell before the recent window: its summary and whether it failed."""
        lines = []
        for number, cell in enumerate(self.history[: max(0, self.cells - self.RECENT)]):
            result = f"failed: {cell['error'].strip().splitlines()[-1]}" if cell["error"] else "ok"
            lines.append(f"cell {number}: {cell['summary']} [{result}]")
        return "\n".join(lines)

    def _recent_cells(self) -> str:
        blocks = []
        for number, cell in enumerate(self.history[-self.RECENT :], start=max(0, self.cells - self.RECENT)):
            parts = [f"### cell {number} · {cell['summary']}", "code:", cell["code"].strip()]
            if cell["output"].strip():
                parts += ["output:", cell["output"].strip()]
            if cell["error"]:
                parts += ["error:", cell["error"].strip()]
            blocks.append("\n".join(parts))
        return "\n\n".join(blocks) or "none yet"


def _clip(output: str, limit: int = 1000) -> str:
    """A cell's output as kept in history: head and tail only. The user already saw all of it."""
    if len(output) <= limit:
        return output
    omitted = len(output) - limit
    note = f"[… {omitted:,} chars omitted; keep data in variables, not output]"
    return f"{output[: limit // 2]}\n{note}\n{output[-limit // 2 :]}"


class Slot:
    """The next cell chosen during the current one. Closed once that cell ends, so a late
    `set_next_cell` from it fails instead of overwriting a newer cell's choice."""

    def __init__(self):
        self.code: str | None = None
        self.summary: str | None = None
        self.closed = False
        self.lock = Lock()

    def submit(self, code: str, summary: str) -> None:
        with self.lock:
            if self.closed:
                raise RuntimeError("This cell has finished; its call_me can no longer choose a cell")
            self.code, self.summary = code, summary  # The last submission wins.

    def close(self) -> None:
        with self.lock:
            self.closed = True


class Agent:
    """A mind with a session and a memory, alive while the loop runs."""

    START = "Start the user's task and submit the first Python cell."
    REPAIR = "The previous cell failed (its error is in the history). Repair it and continue the task."

    def __init__(self, mind: Mind, *, cwd: str | Path | None = None):
        self.mind = mind
        self.cwd = Path(cwd).expanduser().resolve() if cwd is not None else None

    def run(
        self,
        task: str,
        *,
        env: IPythonEnv | None = None,
        on_cell: Callable[[int, str, str], None] | None = None,
        on_result: Callable[[int, CellResult], None] | None = None,
        max_failures: int = 3,
    ) -> None:
        """One life: a fresh session and memory, then cells one after another until one quits
        (a trampoline: each cell only chooses the next one, and this loop runs them all).

        Pass an environment for input/output and an on_cell callback to observe execution.
        Without them, the agent runs with captured output and no user input.
        on_result receives each cell's final validated result, including the cell that quits.
        The configured cwd must exist; the caller's working directory is restored afterward.
        """
        with chdir(self.cwd or Path.cwd()):
            env = env if env is not None else IPythonEnv()
            memory = Memory(task)
            source_code = {
                f"core/{name}": Path(__file__).with_name(name).read_text(encoding="utf-8")
                for name in ("agent.py", "env.py")
            }

            def quit_() -> None:
                raise Quit

            code, summary = _wake_cell(self.START), "Start the task."
            failures = 0
            while True:
                slot = Slot()
                # Rebind every cell, so a cell that overwrites or mutates these cannot break later cells.
                env.bind(
                    call_me=partial(
                        self.mind.wake, submit=slot.submit, context=lambda: memory.context(env.variables())
                    ),
                    call_llm=self.mind.query,
                    quit=quit_,
                    history=memory.snapshot(),
                    var_docs=memory.var_docs,
                    source_code=source_code.copy(),
                )
                if on_cell is not None:
                    on_cell(memory.cells, summary, code)
                result = env.execute(code)
                slot.close()
                if not result.quit and result.error is None and slot.code is None:
                    result.error = "The cell finished without choosing a next cell via set_next_cell."
                if on_result is not None:
                    on_result(memory.cells, result)
                if result.quit:
                    return
                memory.record(code, summary, result)

                if result.error is None:
                    failures, code, summary = 0, slot.code, slot.summary
                    continue
                failures += 1
                if failures >= max_failures:
                    raise RuntimeError(f"{failures} consecutive cells failed; last error:\n{result.error}")
                # Side effects of the failed cell are not rolled back; the mind repairs from here.
                code, summary = _wake_cell(self.REPAIR), "Repair the failed cell."


def _wake_cell(message: str) -> str:
    """A cell that only wakes the mind with `message`; the mind chooses the next cell."""
    return f"response = call_me(input={message!r})\nprint(response.output_text)"
