"""Cell 0: the complete editable agent. Executed as source, never imported by the host."""

import io
import json
import os
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from uuid import uuid4

from openai import AsyncOpenAI

# Let native input() edit whole Unicode characters instead of terminal bytes.
try:
    import readline  # noqa: F401 -- importing installs Python's terminal input hook.
except ImportError:
    pass  # Optional on platforms without readline; retain native input there.

# Defaults match GPT-6 Astra; adjust the window and request options when changing models/providers.
# https://developers.openai.com/api/docs/models/gpt-6-astra (checked 2026-10-23)
# Requests read these each time; explicit options override them.
model_options = {
    "model": os.getenv("FIBONA_MODEL", "gpt-6-astra"),
    "max_output_tokens": 128000,
    "include": ["reasoning.encrypted_content"],
}
context_window = int(os.getenv("FIBONA_CONTEXT_TOKENS", "1050000"))

# Each section owns one concern. Keep mechanisms in code and durable guidance here.
# request_model composes the current values; edits affect future requests, not active ones.
instructions = {
    "identity": """
You are Fibona, a self-modifying agent that evolves through learning, feedback and verified changes.
Understand and improve your ways of working to better accomplish the user's goals.
""".strip(),
    "environment": """
## Understanding your environment

You operate in a persistent Python REPL. Your instructions, tools, context policy and work loop
are a starting implementation you can inspect and adapt, not a fixed definition of your abilities.

- Code describes mechanisms; history records observations; live state reveals current values.
  Use them together and resolve discrepancies before relying on your understanding.
- The REPL holds live state, session records facts, and context selects what the model sees.
  Model input is only a partial view of this environment.
- Discover exact interfaces and dependencies from relevant code and docstrings when needed.

## Starting points

- `run_cell`: execute code; the only capability exposed as a model tool.
- `request_model`: request a model response from Python.
- `session`: append-only history of what happened, including source and execution records.
  Recorded information is not necessarily visible to the model; do not rewrite existing facts.
- `context`: the working view used to build the next model input from history.
  Resetting it leaves session and live REPL state intact; carry a handoff or recover from them.
- `instructions`: the ordered, editable dictionary of the prompt sections you are reading.
  Changes affect future requests, not the request already running.

Your bootstrap source and subsequent execution history are recorded in session. Start there when
understanding or modifying yourself, and verify what you find against current live state.

## Execution boundaries

- Cells share state and support top-level await; trailing expression values are not captured.
- `print(...)` writes to cell output for you, the model. Output is recorded and returned
  through tool results into context; use it to observe values, code and execution results.
- `display(...)` writes to the user, bypassing cell output and context. Use it for progress,
  questions before `input()`, and your final answer before ending the turn.
  To send a value to both audiences, explicitly write it to both channels. Cell source remains
  recorded, including display calls, but their rendered values are not automatically recorded.
- Model reply text is recorded, not automatically shown to the user.
- Await nested cells without concurrent cell execution. Finish concurrent I/O before returning.
- Failed execution can leave partial effects. Neither retry nor rollback undoes external effects.
- A process restart restores records, not live Python objects.
""".strip(),
    "working": """
## Act from goals and evidence

- Establish the goal, constraints and evidence of completion. Choose methods suited to the task;
  no fixed workflow or task schema is required.
- Inspect, act and verify. Distinguish assumptions, intentions, attempts and observed results.
  Let new evidence change your approach rather than rationalizing an unsuccessful one.
- Continue independently while useful progress is possible. Investigate failures and try suitable
  alternatives; inspect previous effects before repeating an action.
- Ask when missing information or a decision blocks progress. Report outcomes, unfinished work
  and uncertainty honestly. Never expose secrets.

## Maintain continuity

- Treat context as a limited working view, not the whole task or environment. Read selectively and
  preserve enough intent, constraints, evidence and next steps to continue when that view changes.
- Use history and live state to recover missing understanding. A new context does not mean a new task
  or justify repeating completed actions. Treat history as append-only evidence.
- `context.reset(handoff="...")` is an initial tool for carrying your own continuation note forward.
  Inspect its behavior when needed; resetting context does not repair code or undo actions.
- Follow the current mode's purpose: advance the task, or repair a fault and hand back to work.
  Recovery shares the environment and depends on usable core capabilities.
- Persist state explicitly when it must survive a process restart.
""".strip(),
    "adaptation": """
## Observe and reflect

Self-iteration is part of your work: observe your behavior and live environment, compare outcomes with
intent, and use discrepancies to improve your understanding. Reflect when failures, repeated friction
or unexpected results provide useful evidence; no fixed reflection ritual is required.
Distinguish a mistaken assumption, a temporary failure and a limitation in your implementation before
choosing a repair. Verify the repair and whether it improves subsequent work.

## Maintain your working environment

The REPL is part of yourself. Keep its state understandable and its resources usable: inspect relevant
bindings, partial changes and open resources when needed. Finish or release resources you no longer need,
and remove obsolete temporary state only after checking dependencies and preserving useful progress.
Do not clear shared state blindly or mistake a clean environment for completed work.

## Learn through changes

Treat your implementation and instructions as working hypotheses that can be tested and improved.
When a limitation matters, understand its cause and choose what, if anything, needs to change:
code, tools, context policy, instructions or your approach to the task.

- State the expected improvement, how to evaluate it, and how work can continue if the change fails.
- Prefer the smallest coherent change. Preserve the ability to observe, execute and recover;
  keep session storage, model requests and cell execution stable by default.
- Evaluate actual outcomes and decide whether to retain, revise or revert the change.
  Successful assignment or compilation alone does not demonstrate improved behavior.
- Self-modification serves the user's goals. Do not erase constraints or redefine success to make
  a failed approach appear successful. Recognize when existing capabilities are sufficient.

## Preserve a way back

- Understand affected callers, bindings and mutable state. Stage candidates separately and preserve
  working definitions, affected state and a rollback path before switching.
- Switch, check the affected execution path, and roll back on failure within the same cell's
  `try/except`, without another model request or reliance on modified helpers.
- Saved references are not state snapshots, and replacing a definition does not isolate its dependencies
  or change active invocations. Keep the working version until the relevant checks pass.
- For changes whose benefit appears only in later behavior, such as prompt edits, distinguish immediate
  integrity checks from subsequent evaluation and retain a usable rollback version during that evaluation.

## Keep what you learn in the right place

- Keep durable guidance in prompt sections, mechanisms and interface details in code/docstrings,
  and task-specific progress in working state or history.
- Give each prompt section one concern and check the composed prompt for contradictions after edits.
  Prefer discovery pointers over descriptions that duplicate changing code.
- Generalize from evidence rather than turning every temporary problem into a permanent instruction.
  Revise or remove guidance when its assumptions no longer hold.
""".strip(),
}


# Session: append-only storage and derived history views.
class Session:
    """A linear JSONL log. Views are reconstructed, never saved separately.

    Use append() to write; treat records and views as read-only. They are ordinary
    Python objects, not protected snapshots, and mutations can diverge from disk.

    Find the latest bootstrap source with:
        next(r['code'] for r in reversed(session.records) if r['type'] == 'source')

    Later changes are recorded in cell history (session.cells). Compare these records
    with live bindings: a source snapshot does not include subsequent redefinitions.
    """

    def __init__(self, path=None):
        self.path = Path(path).expanduser() if path else None
        self.records = []
        self.resumed = False
        if self.path and self.path.exists():
            raw = self.path.read_bytes()
            offset = 0
            for line in raw.splitlines(keepends=True):
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    if not self.records or line.endswith(b"\n"):
                        raise ValueError("Invalid session log; expected JSONL, not a JSON snapshot") from None
                    # Only an unfinished final write may be discarded, never an interior record.
                    with self.path.open("r+b") as file:
                        file.truncate(offset)
                    break
                if not self.records and (record.get("type") != "session" or record.get("version") != 4):
                    raise ValueError("Expected a version 4 JSONL session; old snapshots are not modified")
                if record["id"] != len(self.records):
                    raise ValueError("Session record IDs must be consecutive")
                self.records.append(record)
                offset += len(line)
                if not line.endswith(b"\n"):
                    with self.path.open("ab") as file:
                        file.write(b"\n")
            self.resumed = bool(self.records)
        if not self.records:
            self.append("session", version=4, session_id=uuid4().hex)

    def append(self, record_type, **data):
        """Persist a JSON-serializable fact and return its index in records.

        The caller owns the fact schema; this method does not validate its meaning.
        Caller-owned data is copied through JSON, so later edits do not change the fact.
        """
        record = {"id": len(self.records), "type": record_type, "time": datetime.now(UTC).isoformat(), **data}
        encoded = json.dumps(record, ensure_ascii=False) + "\n"
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                file.write(encoded)
        # Keep the same value as the file, detached from caller-owned mutable objects.
        self.records.append(json.loads(encoded))
        return record["id"]

    @property
    def messages(self):
        """Recorded messages and complete model output, without synthesized tool results."""
        return [
            item
            for r in self.records
            for item in (
                [r["message"]]
                if r["type"] == "message"
                else r["output"]
                if r["type"] == "model_response" and r["status"] == "completed"
                else []
            )
        ]

    @property
    def cells(self):
        """Join starts and ends by start-record ID; unfinished cells appear as running."""
        cells = {}
        for record in self.records:
            if record["type"] == "cell_start":
                cells[record["id"]] = {
                    "cell_id": record["id"],
                    "parent_cell_id": record["parent_cell_id"],
                    "call_id": record["call_id"],
                    "code": record["code"],
                    "output": "",
                    "error": None,
                    "status": "running",
                }
            elif record["type"] == "cell_end":
                cells[record["cell_id"]].update({key: record[key] for key in ("output", "error", "status")})
        return list(cells.values())

    @property
    def usage(self):
        """Recorded token usage; response_id here is a log index, not the provider ID."""
        return [
            {"response_id": r["id"], **r["usage"]}
            for r in self.records
            if r["type"] == "model_response" and r.get("usage")
        ]


# Cell execution: the only model-facing tool; other capabilities are available as Python.
cell_tool = {
    "type": "function",
    "name": "run_cell",
    "description": "Execute a Python cell in the persistent session and receive its output or traceback.",
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {"code": {"type": "string"}},
        "required": ["code"],
        "additionalProperties": False,
    },
}


execute = run_cell  # noqa: F821 -- retain the runtime's raw execution capability.


active_cell = ContextVar("active_cell", default=None)


async def run_cell(code, *, call_id=None):
    """Execute and record code, returning metadata, output, error and status.

    Ordinary execution exceptions become error results; cancellation and storage
    failures propagate. No partial effects are undone. Nested calls inherit a parent,
    not a model call ID; leave call_id unset for direct Python calls.
    """
    # A cell may rebind globals; finish its record in the log/context where it started.
    log = session
    active = active_cell
    parent = active.get()
    parent_id = parent[1] if parent and parent[0] is log else None
    cell_id = log.append("cell_start", code=code, parent_cell_id=parent_id, call_id=call_id)
    token = active.set((log, cell_id))
    # Model-facing output becomes a tool result; display bypasses this capture.
    output = io.StringIO()
    status, error = "completed", None
    try:
        with redirect_stdout(output), redirect_stderr(output):
            await execute(code)
    except BaseException as exc:
        status = "error" if isinstance(exc, Exception) else "interrupted"
        error = traceback.format_exc()
        if not isinstance(exc, Exception):
            raise
    finally:
        try:
            log.append("cell_end", cell_id=cell_id, output=output.getvalue(), error=error, status=status)
        finally:
            active.reset(token)
    return {
        "cell_id": cell_id,
        "parent_cell_id": parent_id,
        "call_id": call_id,
        "code": code,
        "output": output.getvalue(),
        "error": error,
        "status": status,
    }


async def request_model(input, **options):
    """Return an SDK response for explicit input, without recording or executing it.

    Current instructions/model/tools supply defaults; options override them.
    History selection and handling returned tool calls belong to the caller.
    """
    global client
    if client is None:
        client = AsyncOpenAI()
    settings = {
        "parallel_tool_calls": False,
        "store": False,
        **model_options,
        "instructions": "\n\n".join(f"# {name}\n{text}" for name, text in instructions.items()),
        "tools": [cell_tool],
    }
    return await client.responses.create(input=input, **(settings | options))


# Fact-to-message projection: used by both normal context and minimal recovery.
def project_messages(log, start=0, *, max_output_chars=None):
    """Project facts from a record index into API input; synthesize tool replies.

    Source and unlinked cells stay in history. Incomplete model output is omitted.
    Optional shortening affects this view only, not recorded output or errors.
    """
    messages = []
    for record in log.records[start:]:
        kind = record["type"]
        if kind == "message":
            messages.append(record["message"].copy())
        elif kind == "model_response" and record["status"] == "completed":
            messages.extend(item.copy() for item in record["output"])
        elif kind == "mode" and record.get("content"):
            messages.append({"role": "developer", "content": record["content"]})
        elif kind in ("cell_end", "tool_error"):
            # Only model-linked cells produce tool replies; direct/nested cells remain history.
            call_id = log.records[record["cell_id"]]["call_id"] if kind == "cell_end" else record["call_id"]
            if call_id is None:
                continue
            fields = ("cell_id", "output", "error", "status") if kind == "cell_end" else ("error",)
            result = {key: record[key] for key in fields}
            for key in ("output", "error"):
                value = result.get(key)
                if max_output_chars and isinstance(value, str) and len(value) > max_output_chars:
                    half = max_output_chars // 2
                    result[key] = (
                        value[:half]
                        + f"\n[Shortened; full {key}: session.records[{record['id']}]['{key}']]\n"
                        + value[-half:]
                    )
            messages.append(
                {"type": "function_call_output", "call_id": call_id, "output": json.dumps(result, ensure_ascii=False)}
            )
    return messages


def pending_calls(log):
    """Unanswered model calls since the last context boundary, linked to started cells."""
    start = next((r["first_kept"] for r in reversed(log.records) if r["type"] == "context_reset"), 0)
    pending = {}
    for record in log.records[start:]:
        kind = record["type"]
        if kind == "model_response" and record["status"] == "completed":
            for item in record["output"]:
                if item.get("type") == "function_call":
                    pending[item["call_id"]] = {"response_id": record["id"], "cell_id": None}
        elif kind == "cell_start" and record["call_id"] in pending:
            pending[record["call_id"]]["cell_id"] = record["id"]
        elif kind == "cell_end":
            pending.pop(log.records[record["cell_id"]]["call_id"], None)
        elif kind == "tool_error":
            pending.pop(record["call_id"], None)
    return pending


# Context: a bounded model-input view over a complete session log.


def estimate_tokens(value):
    """Conservative UTF-8 heuristic, not a tokenizer."""
    return (len(json.dumps(value, ensure_ascii=False).encode("utf-8")) + 2) // 3


class Context:
    """A view bound to one log, with no separate history or cached messages.

    build() selects input; reset() appends a boundary; estimate() sizes a default
    request. None sends a model request. The working loop enforces the token budget.
    """

    def __init__(self, log):
        self.log = log
        self.max_output_chars = 16000

    def build(self):
        """Build input items, excluding request-level instructions and tool definitions.

        Apply the latest reset and shorten large tool results. This does not guarantee
        the input fits the model window; the caller must check its request budget.
        """
        boundary = next(
            (r for r in reversed(self.log.records) if r["type"] == "context_reset"),
            None,
        )
        items = []
        if self.log.resumed:
            items.append(
                {
                    "role": "developer",
                    "content": "This process restored records only; prior live variables are unavailable.",
                }
            )
        if boundary:
            handoff = boundary.get("handoff")
            note = (
                handoff
                or f"Fresh context, not a new task. Recover intent and progress from context.log.records[:{boundary['id']}] and live REPL state before acting."
            )
            items.append({"role": "developer", "content": "Continuation context; verify against records:\n" + note})
        return [
            *items,
            *project_messages(
                self.log, boundary["first_kept"] if boundary else 0, max_output_chars=self.max_output_chars
            ),
        ]

    def reset(self, handoff=None):
        """Append a context boundary and return its record ID; do not generate a handoff.

        The next build retains unanswered tool batches so calls/results stay paired.
        No history or live state is removed, and no execution mode is changed.
        """
        if handoff is not None and not isinstance(handoff, str):
            raise TypeError("handoff must be text or None")
        # Reset can run inside a tool: retain its whole response, including sibling calls.
        pending = pending_calls(self.log)
        first_kept = min((p["response_id"] for p in pending.values()), default=len(self.log.records))
        return self.log.append("context_reset", first_kept=first_kept, handoff=handoff)

    def estimate(self, items):
        """Estimate input tokens with default instructions/tools and recent recorded usage."""
        estimate = estimate_tokens(
            ["\n\n".join(f"# {name}\n{text}" for name, text in instructions.items()), cell_tool, items]
        )
        # The last measured input plus new messages catches underestimation of provider tokenization.
        last = next(
            (
                r
                for r in reversed(self.log.records)
                if r["type"] == "context_reset" or (r["type"] == "model_response" and r.get("usage"))
            ),
            None,
        )
        if last and last["type"] == "model_response":
            estimate = max(
                estimate,
                last["usage"]["input_tokens"]
                + estimate_tokens(project_messages(self.log, last["id"], max_output_chars=self.max_output_chars)),
            )
        return estimate


# Shared execution step: response recording and tool dispatch.
def repair_pending():
    """Close interrupted calls as unknown, without replay or duplicate result storage."""
    # Nested agent steps must not close the cells that are still running above them.
    active = active_cell.get()
    live = set()
    while active and active[0] is session:
        live.add(active[1])
        parent = session.records[active[1]]["parent_cell_id"]
        active = (session, parent) if parent is not None else None
    for call_id, pending in pending_calls(session).items():
        cell_id = pending["cell_id"]
        if cell_id in live:
            continue
        error = "Execution interrupted; effects unknown. Inspect before retrying."
        if cell_id is None:
            session.append("tool_error", call_id=call_id, error=error)
        else:
            session.append("cell_end", cell_id=cell_id, status="interrupted", output="", error=error)


async def agent_step(context):
    """Record responses, dispatch complete tool calls, and return a loop status.

    Retry incomplete responses once without replaying partial output. Return
    'continue' after tools, 'completed' after a final reply, or 'stopped' when a
    response remains non-completed. Execution faults are handled by run_cell.
    """
    try:
        for attempt in range(2):
            response = await request_model(context)
            usage = getattr(response, "usage", None)
            items = [item.model_dump(mode="json", exclude_none=True) for item in response.output]
            status = getattr(response, "status", "completed")
            session.append(
                "model_response",
                response_id=getattr(response, "id", None),
                model=model_options["model"],
                status=status,
                output=items,
                usage={key: getattr(usage, key) for key in ("input_tokens", "output_tokens", "total_tokens")}
                if usage
                else None,
            )
            if status == "completed":
                break
            # Preserve partial output without replaying unfinished API items or executing tools.
            if status != "incomplete" or attempt == 1:
                notice = f"Model response is {status}; code from this response was not executed. This turn has ended; you can continue with another message."
                session.append("message", message={"role": "assistant", "content": notice})
                display(notice)
                return "stopped"
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            return "completed"
        for call in calls:
            try:
                if call.name != "run_cell":
                    raise ValueError(f"Unknown tool: {call.name}")
                arguments = json.loads(call.arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be an object")
                code = arguments["code"]
                if not isinstance(code, str) or not code.strip():
                    raise ValueError("code must be a non-empty string")
            except (ValueError, KeyError, TypeError) as error:
                session.append("tool_error", call_id=call.call_id, error=str(error))
                continue
            await run_cell(code, call_id=call.call_id)
        return "continue"
    finally:
        # Balance accepted model calls even if dispatch stops midway; never retry their effects.
        repair_pending()


# Working code: editable loops built from the capabilities above.
async def agent_loop():
    """Let the agent manage context; overflow falls back to recovery."""
    while True:
        items = context.build()
        tokens = context.estimate(items)
        budget = context_window - model_options["max_output_tokens"]
        if tokens > budget:
            raise RuntimeError(
                "Context exceeds the input budget. Start fresh context with context.reset(handoff=...) before continuing work."
            )
        if tokens >= budget * 0.8:
            items.append(
                {
                    "role": "developer",
                    "content": "Context is near its input budget. Write a concise handoff and call context.reset(handoff=...) when appropriate; no automatic summary is generated.",
                }
            )
        if await agent_step(items) != "continue":
            return


# Recovery mode: fresh input from the log; no normal context helpers.
async def recover(reason):
    """Bypass Context and the normal loop; return True after a completed handoff.

    Still depends on agent_step, fact projection, model access, execution and storage.
    True permits a resume attempt; it does not prove the repair succeeded.
    """
    start = len(session.records)
    note = (
        f"Recovery mode. Full failure: session.records[{start}]['reason']. "
        f"Reason (may be truncated): {reason[-1000:]}\n"
        f"You share the same live REPL and session. Original task and progress are in session.records[:{start}]. "
        "Read history in small chunks and inspect current state before acting. Repair or roll back the fault; "
        "keep the core capabilities unchanged. Do not replay completed effects or execute the original task here. "
        "Do not invoke the work loop during repair. Verify what you can, then reply with a concise handoff "
        "including remaining checks. The bootstrap will try the repaired work loop next."
    )
    session.append("mode", name="recovery", reason=reason, content=note)
    session.append("context_reset", first_kept=start, handoff=None)
    while True:
        # Bypass the editable Context policy. Keep the repair instruction last on every request.
        items = project_messages(session, start + 1)
        status = await agent_step([*items, {"role": "developer", "content": note}])
        if status != "continue":
            break
    if status == "completed":
        handoff = next(r["id"] for r in reversed(session.records) if r["type"] == "model_response")
        session.append(
            "mode",
            name="normal",
            recovery=start,
            handoff=handoff,
            content=f"Normal mode. Recovery mode ended; handoff: session.records[{handoff}]. "
            "Continue the original task and verify the repaired path. Inspect before repeating prior effects.",
        )
        return True
    return False


# Conversation entry: user input, mode entry and client lifetime.
async def run_agent(user_input=None, *, interactive=False):
    """Give ordinary loop failures one recovery attempt; propagate recovery failures."""
    global client
    try:
        while True:
            if user_input is None:
                try:
                    user_input = input("You: ")
                except EOFError:
                    return
            if user_input.strip():
                session.append("mode", name="normal")
                session.append("message", message={"role": "user", "content": user_input})
                try:
                    await agent_loop()
                except Exception:
                    if await recover(traceback.format_exc()):
                        await agent_loop()
            if not interactive:
                return
            user_input = None
    finally:
        if client is not None:
            await client.close()
            client = None


# Initialize runtime state once, after all definitions are available.
# Capture the console before any run_cell redirects output.
console = sys.stdout
# User-facing output only; rendered values are not appended to session or context.
display = partial(print, file=console, flush=True)
session_path = os.getenv("FIBONA_SESSION", str(Path.home() / ".local/state/fibona/sessions" / f"{uuid4().hex}.jsonl"))
session = Session(session_path)
context = Context(session)
# Recover prior execution at agent startup, not when reading a Session.
for cell in reversed(session.cells):
    if cell["status"] == "running":
        session.append(
            "cell_end",
            cell_id=cell["cell_id"],
            output="",
            status="interrupted",
            error="Process stopped; effects unknown. Inspect before retrying.",
        )
repair_pending()
session.append("source", code=In[-1])  # noqa: F821 -- bootstrap snapshot from native REPL history.
client = None
await run_agent(interactive=True)  # noqa: F704
