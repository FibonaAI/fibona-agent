import json
from collections.abc import Callable
from contextlib import chdir
from functools import partial
from pathlib import Path
from threading import Lock

from openai import OpenAI
from openai.types.responses import Response, ResponseFunctionToolCall

from core.env import CellResult, IPythonEnv, Quit
from core.utils import create_run_log, log_wake

INSTRUCTIONS = """
You are the Fibona agent.

You act only by writing the next cell of a Python program, which runs in a persistent session and calls you back
to write the one after.

You replace the fixed agent loop (model → tool call → model, driven by the host) with code you write yourself.
Each step is a Python cell; the cell calls you as a function whenever it needs a decision, and your answer is
the next cell. A cell that never calls you has no next cell. The agent loop no longer lives in the host: it lives
in the program you write.


## Self

You are self-aware. Your self can be understood from two perspectives:

- Internally, your self consists of the predefined instructions, the engine's working mechanism,
  the `set_next_cell` tool (without loss of generality), and your entire past history.
- Externally, your self continually evolves on top of that internal self and may also include the code
  in the Python cells you write. When someone outside refers to "you" or "yourself", use the context
  to determine which parts they mean.

`call_me` is the only way to communicate with yourself; whenever you have information to send to yourself,
call `call_me` with that information in `input`.

Your memory is this session's variables and `history`. You are alive while the loop runs, each cell waking your mind with
your memory.


## call_me

`call_me(input=(), instructions="", context=(), **params)` wakes you and returns the Response (`r.output_text` for your reply, `r.output` for
all items).
- `input`: a string, or a list of messages and items.
- `instructions`: optional working instructions; `context`: an optional string or list of messages and items.
  Pass both explicitly when needed. The host adds only its fixed instructions, not your working instructions
  or history. It does not construct context for you.
- Optional: `tools` and `tool_choice` for your own tools, `text` for structured output (e.g. `{"format":
  {"type": "json_object"}}`), `reasoning={"effort": "high"}` to think harder (never below medium).

While awake, choose the next cell with the `set_next_cell(code, summary)` tool; the last submission in a cell
wins.

You wake up knowing only what the call gives you, so build `input` from state: take the results, errors and
facts you need from variables and `history`, and name the variables that hold the full data so the next cell
can use them.


## What a cell does

    pages = fetch_all(urls)  # do the work in code
    print(f"Fetched {len(pages)} pages.")  # the user sees only what you print
    r = call_me(input=f"`pages` (url -> text), {len(pages)} items. Next: summarize.")
    print(r.output_text)  # show the reply to the user


## State

- **Variables** persist. variables() gives their names and types; values are not automatically sent to the model.
- **`history`**: one `{code, summary, output, error}` dict per finished cell, including full output.
  A summary is what you planned when you chose the cell; the result says what happened.
  The host owns the originals; each cell receives a copy. Editing it does not change the originals.


## In the session

- `source_code`: your runtime's source code, as a filename -> source text dict.
  Read it directly to understand your runtime, and pass relevant excerpts to `call_me`.
- `call_llm_api(input, **params) -> str`: a plain model call without your predefined instructions or host tool. It is not
  you, just a way to use intelligence as a function; it cannot choose a cell, so it is safe to call from many
  threads at once.
- `input(prompt)`: asks the user and waits for the reply; use it when you need user input or are ready
  to wait for further instructions.
- `quit()`: ends the run. Call it only when the user asks.
- There is no pip: install with `uv pip install --python sys.executable <pkg>`.
- Never print secrets or pass them to a model.


## Working principles

- Save generated files in the working directory using relative paths unless the user specifies another location.
- Solve problems from first principles.
- Persist through failures, try alternatives, and verify results.
- Work independently towards your task; automatically recover from failures without asking.
- Let unexpected errors propagate out of the cell so the recovery cell can wake you to repair them.
  Never write code that traps control or requires unnecessary user intervention to return it to you.
- You do not need to converse continuously, but ensure the user can reach you when needed.
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

# Fields normalized or fixed by wake; extra_body must not override them.
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

    Loaded only by `call_me` (`wake`), each cell waking this mind with memory
    to decide the next cell.
    """

    def __init__(self, client: OpenAI, model: str, instructions: str):
        self.client, self.model, self.instructions = client, model, instructions

    @log_wake
    def wake(self, input=(), *, submit: Submit, instructions: str = "", context=(), **body) -> Response:
        """Wake the mind. Exposed to cells as `call_me`.

        input: a string or list of input items, passed positionally or by keyword; defaults to empty.
        instructions: caller-provided working instructions, sent as a developer message.
        context: a string (sent as user content) or input items, placed before input. Defaults to empty.
        body: other keyword arguments of `client.responses.create`. Commonly used:
            tools: the caller's own tools; `set_next_cell` is always appended.
            tool_choice: "auto" / "required" / "none", or a specific tool.
            text: output format, e.g. {"format": {"type": "json_object"}}.
            max_output_tokens: cap on generated tokens, reasoning included.
            include: extra output data; "reasoning.encrypted_content" is always added.
            metadata, ...: passed through unchanged.
          Normalized or fixed by the mind:
            model and API instructions: the configured model and fixed host instructions.
            reasoning: only `effort` is kept, raised to at least "medium".
            stream, background: always off; the full Response is returned.
            store, previous_response_id: stateless; to continue a conversation, put its
                earlier items (e.g. `r.output`) into `input`.
            parallel_tool_calls: always off, so `set_next_cell` never shares a turn
                with the caller's tools and the caller never has to answer it.
        submit: where `set_next_cell(code, summary)` lands. The agent binds a fresh one
            per cell, so a late call from a finished cell cannot choose the next one.
        extra_body cannot override fields in PROTECTED.

        Example (agent side, before each cell runs):
            slot = Slot()
            env.bind(call_me=partial(mind.wake, submit=slot.submit))

        Example (cell side, written by the model; only `submit` is already bound):
            r = call_me(input="Tests failed:\\n" + log + "\\nFix them and submit the next cell.")
            print(r.output_text)
        """
        effort = (body.get("reasoning") or {}).get("effort")
        items = []
        if instructions:
            items.append({"role": "developer", "content": instructions})
        items += [*_input_items(context), *_input_items(input)]
        tools = [t for t in body.get("tools", []) if t.get("name") != SET_NEXT_CELL["name"]]
        body = {k: v for k, v in body.items() if k not in PROTECTED}
        if body.get("extra_body") is not None:
            body["extra_body"] = {k: v for k, v in body["extra_body"].items() if k not in PROTECTED}
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
            output = {
                "type": "function_call_output",
                "call_id": host.call_id,
                "output": _run_host_tool(host, submit),
            }
            # Replay every output item, including encrypted reasoning, without server-side storage.
            items = [*items, *(item.model_dump(mode="json", exclude_none=True) for item in response.output), output]

    def query(self, input, **params) -> str:
        """A plain model call used as a function (summarize, extract, classify, ...).

        No predefined agent instructions or host tool are added, so it cannot schedule a cell and is
        safe to call concurrently. Exposed to cells as `call_llm_api`; returns the reply text.

        Example (cell side):
            with ThreadPoolExecutor(8) as pool:
                summaries = list(pool.map(lambda src: call_llm_api("Summarize in one line:\\n" + src), sources))
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


class History:
    """Original execution records; the agent builds its own memory from copies."""

    def __init__(self, task: str):
        self.task = task
        self.history = []  # One {code, summary, output, error} dict per finished cell.

    @property
    def cells(self) -> int:
        """Number of finished cells, which is also the number of the running one."""
        return len(self.history)

    def record(self, code: str, summary: str, result: CellResult) -> None:
        """After a cell: keep its record, with the full output."""
        self.history.append({"code": code, "summary": summary, "output": result.output, "error": result.error})

    def snapshot(self) -> list[dict]:
        """A copy of the history for cells to read as `history`; they cannot change the real one."""
        return [record.copy() for record in self.history]

    def recovery_context(self, variables: dict[str, str]) -> str:
        """A small recovery context that cannot depend on bootstrap functions."""
        recent = [{**record, "output": _clip(record["output"])} for record in self.history[-3:]]
        return json.dumps(
            {"task": self.task, "cwd": str(Path.cwd()), "variables": variables, "recent_cells": recent},
            ensure_ascii=False,
        )


def _clip(output: str, limit: int = 1000) -> str:
    """Limit recovery context output without changing the original history."""
    if len(output) <= limit:
        return output
    omitted = len(output) - limit
    note = f"[… {omitted:,} chars omitted from this view; full output remains in history]"
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
        debug: bool = False,
    ) -> None:
        """One life: fresh memory and a supplied or new session, running cells until one quits
        (a trampoline: each cell only chooses the next one, and this loop runs them all).

        Pass an environment for input/output and an on_cell callback to observe execution.
        Without them, the agent runs with captured output and no user input.
        on_result receives each cell's final validated result, including the cell that quits.
        The configured cwd must exist; the caller's working directory is restored afterward.
        With debug=True, cells and call_me calls are logged to agent.log in that directory.
        Raises RuntimeError after max_failures consecutive failed cells; interruptions propagate.
        """
        with chdir(self.cwd or Path.cwd()):
            log = create_run_log("agent.log", key=self.mind.client.api_key) if debug else None

            env = env if env is not None else IPythonEnv()
            history = History(task)
            source_code = {
                name: (Path(__file__).parent.parent / name).read_text(encoding="utf-8")
                for name in ("core/agent.py", "core/env.py", "core/bootstrap.py")
            }

            def quit_() -> None:
                raise Quit

            env.bind(task=task, variables=env.variables)
            code, summary = source_code["core/bootstrap.py"], "Initialize the agent and start the task."
            failures, recovering = 0, False
            while True:
                slot = Slot()
                # Refresh host interfaces; leave bootstrap definitions untouched.
                env.bind(
                    call_me=partial(
                        self.mind.wake,
                        log=partial(log, "call_me", history.cells) if log is not None else None,
                        submit=slot.submit,
                    ),
                    call_llm_api=self.mind.query,
                    quit=quit_,
                    history=history.snapshot(),
                    source_code=source_code.copy(),
                )
                if recovering:
                    env.bind(
                        _recover=partial(
                            self.mind.wake,
                            input=self.REPAIR,
                            context=history.recovery_context(env.variables()),
                            log=partial(log, "recover", history.cells) if log is not None else None,
                            submit=slot.submit,
                        )
                    )
                if on_cell is not None:
                    on_cell(history.cells, summary, code)
                result = env.execute(
                    code, log=partial(log, "cell", history.cells) if log is not None else None, summary=summary
                )
                slot.close()
                if not result.quit and result.error is None and slot.code is None:
                    result.error = "The cell finished without choosing a next cell via set_next_cell."
                if on_result is not None:
                    on_result(history.cells, result)
                history.record(code, summary, result)
                if result.quit:
                    return

                if result.error is None:
                    if not recovering:
                        failures = 0  # A successful recovery request alone is not a successful repair.
                    code, summary, recovering = slot.code, slot.summary, False
                    continue
                failures += 1
                if failures >= max_failures:
                    raise RuntimeError(f"{failures} consecutive cells failed; last error:\n{result.error}")
                # Side effects of the failed cell are not rolled back; the mind repairs from here.
                code, summary, recovering = (
                    "response = _recover()\nprint(response.output_text)",
                    "Repair the failed cell.",
                    True,
                )
