import json
import logging
from collections.abc import Callable
from contextlib import chdir
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from threading import Lock
from uuid import uuid4

from openai import APIError, OpenAI
from openai.types.responses import Response, ResponseFunctionToolCall, ResponseFunctionToolCallOutputItem

from core.env import CellResult, IPythonEnv, Quit
from core.utils import log_info, run_logger

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

`call_me(input, **params)` wakes you and returns the Response (`r.output_text` for your reply, `r.output` for
all items).
- `input`: a string, or a list of messages and items.
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

- **Variables** persist. Inspect them with `globals()`; values are not automatically sent to you.
- **`history`**: one `{code, summary, output, error}` dict per finished cell, with the full output.
  Summaries record intent; output and errors record what happened. Each cell receives a copy of the host's records.
- Choose what to include in `input`. The host does not select or send working context for you.

## In the session

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

    Loaded only by `call_me` (`wake`): each cell wakes this mind with the input it builds
    to decide the next cell.
    """

    def __init__(self, client: OpenAI, model: str, instructions: str):
        self.client, self.model, self.instructions = client, model, instructions

    def wake(self, input=(), *, submit: Submit, logger=None, **body) -> Response:
        """Wake the mind. Exposed to cells as `call_me`.

        The returned output contains every round's items and host tool results, in order.
        Usage sums known values across rounds; its custom `incomplete` flag is True if any round
        omits usage, in which case the totals may undercount. Otherwise the flag is False.
        It is None only if no round reports usage.
        Other Response fields (id, status, etc.) describe the final API response.

        input: a string or list of input items, passed positionally or by keyword; defaults to empty.
        body: other keyword arguments of `client.responses.create`. Commonly used:
            tools: the caller's own tools; `set_next_cell` is always appended.
            tool_choice: "auto" / "required" / "none", or a specific tool.
            text: output format, e.g. {"format": {"type": "json_object"}}.
            max_output_tokens: cap on generated tokens, reasoning included.
            include: extra output data; "reasoning.encrypted_content" is always added.
            metadata, ...: passed through unchanged.
          Normalized or fixed by the mind:
            model, instructions: the mind itself.
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
        items = _input_items(input)
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
        outputs = []
        usage = None
        usage_incomplete = False  # Whether any round omitted usage.
        metadata = {"request_id": uuid4().hex, "started_at": datetime.now(UTC).isoformat(), "source": "call_me"}
        log_info(logger, "model_request", **metadata, request={**body, "input": items})
        while True:
            request = {**body, "input": items}
            try:
                response = self.client.responses.create(**request)
                if response.status == "failed":  # e.g. OpenRouter's provider errors after generation began.
                    raise APIError(f"Response {response.id} failed: {response.error}", request=None, body=None)
            except BaseException as exc:
                log_info(
                    logger,
                    "model_error",
                    **metadata,
                    error={"type": type(exc).__name__, "message": str(exc)},
                    output=outputs,
                )
                raise
            outputs.extend(response.output)
            usage_incomplete = usage_incomplete or response.usage is None
            usage = _add_usage(usage, response.usage)
            next_cell_call = _find_next_cell_call(response)
            if next_cell_call is None:
                if usage is not None:
                    usage.incomplete = usage_incomplete
                response = response.model_copy(update={"output": outputs, "usage": usage})
                log_info(logger, "model_response", **metadata, response=response)
                return response
            output = ResponseFunctionToolCallOutputItem(
                id="fco_" + uuid4().hex,
                type="function_call_output",
                call_id=next_cell_call.call_id,
                status="completed",
                output=_run_host_tool(next_cell_call, submit),
            )
            outputs.append(output)
            # Replay every output item, including encrypted reasoning, without server-side storage.
            items = [
                *items,
                *(item.model_dump(mode="json", exclude_none=True) for item in response.output),
                output.model_dump(mode="json", exclude_none=True),
            ]

    def query(self, input, *, logger=None, **params) -> str:
        """A plain model call used as a function (summarize, extract, classify, ...).

        No predefined agent instructions or host tool are added, so it cannot schedule a cell and is
        safe to call concurrently. Exposed to cells as `call_llm_api`; returns the reply text.

        Example (cell side):
            with ThreadPoolExecutor(8) as pool:
                summaries = list(pool.map(lambda src: call_llm_api("Summarize in one line:\\n" + src), sources))
        """
        params = {k: v for k, v in params.items() if k not in {"stream", "background", "previous_response_id"}}
        params.setdefault("model", self.model)
        request = {**params, "store": False, "input": _input_items(input)}
        metadata = {"request_id": uuid4().hex, "started_at": datetime.now(UTC).isoformat(), "source": "call_llm_api"}
        log_info(logger, "model_request", **metadata, request=request)
        try:
            response = self.client.responses.create(**request)
            if response.status == "failed":  # e.g. OpenRouter's provider errors after generation began.
                raise APIError(f"Response {response.id} failed: {response.error}", request=None, body=None)
        except BaseException as exc:
            log_info(logger, "model_error", **metadata, error={"type": type(exc).__name__, "message": str(exc)})
            raise
        log_info(logger, "model_response", **metadata, response=response)
        return response.output_text


def _add_usage(total, current):
    """Accumulate usage without changing the original responses; unknown details stay unknown."""
    if current is None:
        return total
    if total is None:
        return current.model_copy(deep=True)
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        setattr(total, field, getattr(total, field) + getattr(current, field))
    for field in ("input_tokens_details", "output_tokens_details"):
        details, incoming = getattr(total, field), getattr(current, field)
        for key in type(details).model_fields:
            a, b = getattr(details, key, None), getattr(incoming, key, None)
            setattr(details, key, a + b if a is not None and b is not None else None)
    if hasattr(total, "cost"):  # A provider extra, e.g. OpenRouter's cost in USD.
        a, b = total.cost, getattr(current, "cost", None)
        total.cost = a + b if a is not None and b is not None else None
    return total


def _input_items(value) -> list:
    """A string becomes one user message; a list of items is used as given."""
    return [{"role": "user", "content": value}] if isinstance(value, str) else list(value)


def _find_next_cell_call(response: Response) -> ResponseFunctionToolCall | None:
    """Return the first set_next_cell call, or None if there is no match."""
    for item in response.output:
        if item.type == "function_call" and item.name == SET_NEXT_CELL["name"]:
            return item
    return None


def _run_host_tool(call: ResponseFunctionToolCall, submit: Submit) -> str:
    """Run a `set_next_cell` call; the JSON result goes back to the mind, so a rejected submission is not raised."""
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
    """The host's execution records; cells receive copies."""

    def __init__(self):
        self.history = []  # One {code, summary, output, error} dict per finished cell.

    @property
    def cell_count(self) -> int:
        """Number of finished cells, which is also the number of the running one."""
        return len(self.history)

    def record(self, code: str, summary: str, result: CellResult) -> None:
        """After a cell: keep its original record, with the full output."""
        self.history.append({"code": code, "summary": summary, "output": result.output, "error": result.error})

    def snapshot(self) -> list[dict]:
        """Copies for cells to read as history; editing them cannot alter the originals."""
        return [record.copy() for record in self.history]


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

    REPAIR = """
The previous cell failed (its error is in history).
If needed, inspect session variables via globals() and history to recover the task and progress.
Repair the failure and continue the program.
""".strip()

    def __init__(self, mind: Mind, *, cwd: str | Path | None = None):
        self.mind = mind
        self.cwd = Path(cwd).expanduser().resolve() if cwd is not None else None

    def run(
        self,
        bootstrap_cell: str,
        *,
        env: IPythonEnv | None = None,
        on_cell: Callable[[int, str, str], None] | None = None,
        on_result: Callable[[int, CellResult], None] | None = None,
    ) -> None:
        """Execute bootstrap_cell verbatim, then run the cells selected by its program.

        Startup policy, user tasks and UI composition belong in the supplied Python code.
        Pass an environment for input/output and an on_cell callback to observe execution.
        Without them, the agent runs with captured output and no user input.
        on_result receives each cell's final validated result, including the cell that quits.
        The configured cwd must exist; the caller's working directory is restored afterward.
        Cells and call_me calls are automatically logged to a separate logs/*.jsonl file for each run
        ($FIBONA_LOG_DIR overrides logs/).
        Interruptions propagate.
        Cells execute synchronously in the calling thread.
        """
        with chdir(self.cwd or Path.cwd()), run_logger() as logger:
            env = env if env is not None else IPythonEnv()
            memory = Memory()

            def quit_() -> None:
                raise Quit

            code, summary = bootstrap_cell, "Run the bootstrap cell."
            while True:
                slot = Slot()
                cell_logger = logging.LoggerAdapter(logger, {"cell": memory.cell_count})
                # Restore runtime bindings each cell; mutations to shared objects still persist.
                env.bind(
                    call_me=partial(
                        self.mind.wake,
                        submit=slot.submit,
                        logger=cell_logger,
                    ),
                    call_llm_api=partial(self.mind.query, logger=cell_logger),
                    quit=quit_,
                    history=memory.snapshot(),
                )
                if on_cell is not None:
                    on_cell(memory.cell_count, summary, code)
                result = env.execute(code, logger=cell_logger, summary=summary)
                slot.close()
                if not result.quit and result.error is None and slot.code is None:
                    result.error = "The cell finished without choosing a next cell via set_next_cell."
                log_info(cell_logger, "cell_result", output=result.output, error=result.error, quit=result.quit)
                if on_result is not None:
                    on_result(memory.cell_count, result)
                if result.quit:
                    return
                memory.record(code, summary, result)

                if result.error is None:
                    code, summary = slot.code, slot.summary
                    continue
                # Side effects of the failed cell are not rolled back; the mind repairs from here.
                message = (
                    self.REPAIR
                    + "\n\n"
                    + json.dumps(
                        {"bootstrap_cell": bootstrap_cell, "code": code[:24000], "error": result.error[-8000:]},
                        ensure_ascii=False,
                    )
                )
                # The recovery cell: wakes the mind with the failure so it can submit a repaired next cell.
                code, summary = _wake_cell(message), "Repair the failed cell."


def _wake_cell(message: str) -> str:
    """A cell that only wakes the mind with `message`; the mind chooses the next cell."""
    return f"response = call_me(input={message!r})\nprint(response.output_text)"
