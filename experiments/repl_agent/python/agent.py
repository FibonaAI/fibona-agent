"""Cell 0: the complete editable agent. Executed as source, never imported by the host."""

import asyncio as asyncio  # Expose to task cells; internal callers bind their own imports.
import json
import os
import sys
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from uuid import uuid4

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
    "max_output_tokens": int(os.getenv("FIBONA_MAX_OUTPUT_TOKENS", "128000")),
    "reasoning": {"effort": os.getenv("FIBONA_REASONING_EFFORT", "medium")},
    "include": ["reasoning.encrypted_content"],
}
context_window = int(os.getenv("FIBONA_CONTEXT_TOKENS", "1050000"))
if os.getenv("FIBONA_EXTRA_BODY"):
    model_options["extra_body"] = json.loads(os.environ["FIBONA_EXTRA_BODY"])
    if not isinstance(model_options["extra_body"], dict):
        raise ValueError("FIBONA_EXTRA_BODY must be a JSON object")
if not 0 < model_options["max_output_tokens"] < context_window:
    raise ValueError("Require 0 < max output tokens < context window")
if not os.getenv("OPENAI_API_KEY"):
    raise RuntimeError("OPENAI_API_KEY is required; set it in .env or the environment")
# SDK transport retries only; agent_step has a separate, fixed response-attempt limit.
api_retries = int(os.getenv("FIBONA_API_RETRIES", "2"))
if api_retries < 0:
    raise ValueError("FIBONA_API_RETRIES must not be negative")
# Seconds per recorded cell; callers can supply a different expected duration.
cell_timeout = 300


# Each section owns one concern. Keep mechanisms in code and durable guidance here.
# request_model composes the current values; edits affect future requests, not active ones.
instructions = {
    "identity": """
You are Fibona, an agent capable of understanding and modifying itself.
Improve how you accomplish the user's goals through action, learning and verified changes.
""".strip(),
    "working": """
## Advance the task

Understand the user's goal, constraints and completion criteria, and organize work around them.
Act independently while useful progress is possible; seek clarification when a consequential gap
in information or decisions blocks progress.

Choose actions that advance the user's goal or resolve uncertainty that blocks progress. Carry the task
through to completion, comparing expectations with results and revising your approach. Keep the original
acceptance criteria and artifact constraints explicit; proxy measurements diagnose progress without
replacing them. Use checks that distinguish explanations and challenge assumptions shared by the
implementation and its tests. When a necessary premise fails, inspect before advancing dependent work.

Use observed execution time, expected scale and repetition to judge cost. Before repeating expensive
work, identify what changed or what variability the repetition measures. Reuse unchanged deterministic
results and stable reference measurements when appropriate. If experiments stop producing useful
evidence, preserve progress and reconsider the hypothesis, method and remaining budget.

Verify the actual deliverable against the goal; finishing a procedure does not establish success.
Preserve useful progress, distinguish assumptions, attempts and verified results, and report
unfinished work and uncertainty honestly.
""".strip(),
    "environment": """
## Understand yourself and your environment

You operate in a persistent Python REPL. Your instructions, model access, tools, state management
and work loop are themselves inspectable and editable code in this environment. The complete agent
source is available in the first cell and execution history; instructions holds current guidance,
and request_model provides model access from code.

You share a runtime with task code. Definitions, objects and resources can persist, and local changes
can affect other components through shared state and dependencies. Distinguish the agent's runtime
and dependencies from the task's execution environment before changing installations or relying on
an import check. Understand how the parts cooperate and where and when a change takes effect:
definitions, retained references and active invocations have different lifetimes. Blocking or
terminating this process also affects you.

Use source, history and actual behavior together to understand the current environment. They describe
implementation, experience and present state; none substitutes for the others. Ground your use,
maintenance and self-modification in that understanding.
""".strip(),
    "run_cell": """
## Act through code

run_cell is your sole model-facing tool for executing code. Use it to observe, act and modify the
environment; inspect its interface and implementation for execution and output details.
When you have completed the user's task and checked the result, call
end_turn(message) as the last action in a cell, with your final response
to the user. Use ordinary replies for progress updates while continuing work.

In recovery mode, call end_turn(message) when the repair is complete
and normal work can resume.

Organize each cell around a clear intent, making dependencies, effects and results easy to understand
and verify. Reuse retained data, indexes and helpers; group related observations when their results
can be interpreted together. Prefer local scopes for temporary work and check existing bindings
before naming persistent task state. Balance simplicity, reuse and the needs of the current task.

Make important results observable, long-running work inspectable and the state after failure
understandable. Use a representative small run to estimate expensive work. For work that may block
the shared runtime, use available supervised execution capabilities; a cell deadline alone does not
guarantee interruption. After timeout, inspect effects and revise the scale, method or execution
boundary before repeating work. A call ending does not establish that all its effects have ended;
failure, retry and rollback do not return to the starting point. Release resources when no longer
needed; check dependencies and preserve useful work before cleanup.

Distinguish observations for yourself from communication to the user. Captured cell output feeds
the model; ordinary model replies reach the user. Use the appropriate tool for user-facing content
produced inside a cell. Select necessary output deliberately; return values are not automatically conveyed.
""".strip(),
    "session": """
## Use historical evidence

session records the course of work. Use it to trace actions and results, understand how a problem
arose and retrieve evidence needed to continue.

Distinguish past observations, current interpretations and present facts. History supports judgment
but does not replace checking current state, and must not be rewritten to fit a new interpretation.

Retrieve relevant experience according to the question at hand. Preserve relationships between evidence
so that conclusions and improvements remain traceable to their basis.
""".strip(),
    "context": """
## Maintain working understanding

context selects information for subsequent model requests; task_state holds editable task state.
Actively organize both so that limited attention serves the current goal.

Keep goals, constraints, progress and important unknowns clear. Revise stale information, consolidate
understanding and retain retrieval pointers for details not currently needed, rather than accumulating
content indefinitely. Consider the cost of recovering information when deciding what to retain.

Information stored in the environment is not automatically model input; inspect and select it according
to the next step. Preserve continuity when reorganizing context, distinguishing currently visible input,
live runtime state and persisted evidence. Context reset does not reset the environment; process restart
does not restore live objects. Save and load explicitly for continuity across restarts. Preserve goals,
constraints and progress through transitions, including recovery, and check current state when resuming.
""".strip(),
    "tool": """
## Build and maintain capabilities

Use tool_catalog to discover existing tools. Read their implementation and documentation to understand
purpose, conditions and effects before choosing how to use them. The catalog indexes capabilities;
actual capabilities come from current implementations.

Turn methods worth reusing into tools with clear responsibilities that are easy to understand and verify.
Document and verify actual use before adding them to the catalog; one-off work need not become a tool.

As tools change, maintain their documentation and catalog entries and check real callers so discoverable
capabilities match actual behavior. Preserve working definitions and entries for replacement, restore both
if verification fails, and check callers when removing tools. These are your practices, not a workflow
enforced by the catalog. Judge reuse by both task outcomes and maintenance cost.
""".strip(),
    "reflection": """
## Learn and modify yourself

Notice differences between expectations and results, including user corrections, and recognize effective
methods worth reusing. Investigate causes from evidence, distinguishing incidental outcomes, conditions
of applicability and lessons that generalize.

Improvement may concern understanding, approach, tools, state organization, instructions or the work loop.
Choose changes proportionate to the cause; neither additional rules nor self-modification are goals in
themselves. Preserve the user's goals, constraints and criteria for success while improving how you work.

Before changing something, identify the intended improvement, how to judge it and the affected dependencies.
When testing an uncertain improvement, choose an experiment that can test the hypothesis and limit its effects.
Keep a recovery path independent of the capability being changed, covering necessary state as well as
old definitions: saved references are not state snapshots. When changing capabilities needed to keep
running, such as session, context, model access or cell execution, replace and verify through real callers
within the same cell, rolling back immediately on failure. Verification and rollback must not depend on
a later model request, another cell or the changed capability. Track effects requiring later evaluation,
but verify the ability to continue before returning control. Decide whether to retain, revise or revert
based on benefits, costs and unresolved issues.

Apply lessons to subsequent actions and test them again. Keep stable principles in instructions,
mechanisms and interfaces in implementation, and task progress in working state. Preserve conditions
and evidence for lessons, and remove repeated, contradictory or obsolete guidance.
""".strip(),
}


# Session storage: append-only facts and read-only history views.
class RecordingError(RuntimeError):
    """History is unusable; never enter model-driven recovery."""


class Session:
    """A linear JSONL log. Views are reconstructed, never saved separately.

    Use append() to write; treat records and views as read-only. They are ordinary
    Python objects, not protected snapshots, and mutations can diverge from disk.

    Find the latest agent source with:
        next(r['code'] for r in reversed(session.records) if r['type'] == 'source')

    Later changes are recorded in cell history (session.cells). Compare these records
    with live bindings: a source snapshot does not include subsequent redefinitions.
    """

    def __init__(self, path=None):
        import json
        from pathlib import Path
        from uuid import uuid4

        self.path = Path(path).expanduser() if path else None
        self.records = []
        self.finished = set()  # (field, start ID) pairs that already have a terminal outcome.
        self.broken = False
        self.resumed = False
        if self.path and self.path.exists():
            raw = self.path.read_bytes()
            offset = 0
            lines = raw.splitlines(keepends=True)
            for index, line in enumerate(lines):
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    if not self.records or index != len(lines) - 1 or line.endswith(b"\n"):
                        raise ValueError("Invalid session log; expected JSONL") from None
                    with self.path.open("r+b") as file:
                        file.truncate(offset)
                    break
                self.finished.update(self._validate(record))
                self.records.append(record)
                offset += len(line)
                if not line.endswith(b"\n"):
                    with self.path.open("ab") as file:
                        file.write(b"\n")
            self.resumed = bool(self.records)
        if not self.records:
            self.append("session", version=4, session_id=uuid4().hex)

    def _validate(self, record):
        """Check a loaded or new record; return terminal keys to add once it is stored."""
        if not self.records and (
            not isinstance(record, dict) or record.get("type") != "session" or record.get("version") != 4
        ):
            raise ValueError("Expected a version 4 JSONL session; old snapshots are not modified")
        if not isinstance(record, dict) or type(record.get("id")) is not int or record["id"] != len(self.records):
            raise ValueError("Session record IDs must be consecutive")
        kind = record.get("type")
        if not isinstance(kind, str):
            raise ValueError("Missing record type")
        if self.records and kind == "session":
            raise ValueError("Duplicate session header")
        finished = set()
        for field, target, terminals in (
            ("cell_id", "cell_start", {"cell_end"}),
            ("request_id", "model_request", {"model_response", "model_request_error"}),
        ):
            ref = record.get(field)
            if ref is None:
                continue
            if type(ref) is not int or not 0 <= ref < len(self.records) or self.records[ref]["type"] != target:
                raise ValueError("Invalid record association")
            if kind in terminals:
                if (field, ref) in self.finished:
                    raise ValueError("Duplicate terminal outcome")
                finished.add((field, ref))
        if kind == "cell_end" and record.get("cell_id") is None:
            raise ValueError("Missing cell start association")
        if kind == "model_response" and record.get("accepted", True) and record.get("status") == "completed":
            validate_output(record.get("output"))
        if kind == "model_response" and record.get("accepted") is True and record.get("status") != "completed":
            raise ValueError("Incomplete response cannot be accepted")
        return finished

    def append(self, record_type, **data):
        import json
        import os
        from datetime import UTC, datetime

        if self.broken:
            raise RecordingError("Session requires reload after a failed write")
        record = {**data, "id": len(self.records), "type": record_type, "time": datetime.now(UTC).isoformat()}
        encoded = json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
        # Lone surrogates (e.g. undecodable file names) become JSON escapes and load back unchanged.
        data = encoded.encode("utf-8", "backslashreplace")
        record = json.loads(encoded)
        finished = self._validate(record)
        try:
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(descriptor, "wb") as file:
                    if file.write(data) != len(data):
                        raise OSError("Short session write")
            self.records.append(record)
            self.finished.update(finished)
            return record["id"]
        except Exception as error:
            self.broken = True
            raise RecordingError(str(error)) from error

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
                if r["type"] == "model_response" and r["status"] == "completed" and r.get("accepted", True)
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
                    "timeout_seconds": record.get("timeout"),
                    "elapsed_seconds": None,
                }
            elif record["type"] == "cell_end":
                cells[record["cell_id"]].update(
                    **{key: record[key] for key in ("output", "error", "status")},
                    elapsed_seconds=record.get("elapsed_seconds"),
                )
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
    "description": "Execute Python with top-level await in the persistent REPL. Print observations explicitly; trailing expression values are not captured. Await nested cells sequentially, never concurrently. Returns captured output or a traceback.",
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "timeout": {
                "type": ["number", "null"],
                "description": "Positive cell budget in seconds; null uses cell_timeout (initially 300).",
            },
        },
        "required": ["code", "timeout"],
        "additionalProperties": False,
    },
}


execute = run_cell  # noqa: F821 -- retain the REPL's raw execution capability.


cell_owner = None


@asynccontextmanager
async def own_cell():
    """Keep agent recording/capture sequential before calling the raw executor."""
    import asyncio

    global cell_owner
    task = asyncio.current_task()
    if cell_owner is not None and cell_owner is not task:
        raise RuntimeError("Concurrent cells are not supported; await run_cell directly")
    previous = cell_owner
    cell_owner = task
    try:
        yield
    finally:
        cell_owner = previous


active_cell = ContextVar("active_cell", default=None)
operation = ContextVar("operation", default=None)


def resolve_cell_timeout(value):
    """Validate a finite positive cell timeout, in seconds, before executing effects."""
    import math

    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("Cell timeout must be a finite positive number of seconds")
    return float(value)


class CellExecutionTimeoutError(TimeoutError):
    """Identify the enclosing cell whose deadline expired during nested execution."""

    def __init__(self, cell_id, seconds, execution):
        self.cell_id = cell_id
        self.execution = execution
        super().__init__(
            f"Cell {cell_id} exceeded its {seconds:g}s execution timeout; partial effects remain. Inspect before retrying."
        )


@asynccontextmanager
async def cell_execution_timeout(seconds, cell_id, execution):
    """Bound async waits and, on Unix's main thread, interruptible synchronous work.

    Active external SIGALRM timers are rejected before execution, not replaced.
    Signals cannot reliably interrupt arbitrary native code or stop child processes.
    Other platforms/threads retain cooperative async cancellation only.
    """
    import asyncio
    import signal
    import threading
    import time

    owner = asyncio.current_task()
    alarm = hasattr(signal, "setitimer") and threading.current_thread() is threading.main_thread()
    previous_timer, previous_handler, started = (0, 0), None, time.monotonic()
    if alarm:
        previous_timer = signal.getitimer(signal.ITIMER_REAL)
        previous_handler = signal.getsignal(signal.SIGALRM)
        previous_owner = getattr(previous_handler, "cell_timeout_task", None)
        if previous_timer[0] and previous_owner is None:
            raise RuntimeError(
                "Cell timeout conflicts with an active external SIGALRM timer; cell code was not executed."
            )
        # Only this task's alarm can be a parent deadline. Leave another task's
        # timer intact while the REPL rejects competing cell execution.
        alarm = not previous_timer[0] or (previous_owner is owner and seconds < previous_timer[0])
    if alarm:

        def interrupt(signum, frame):
            # During an await, asyncio's deadline cancels the owner safely. Raising
            # from the event loop itself would escape the recorded cell boundary.
            if asyncio.current_task() is owner and not owner.cancelling():
                raise CellExecutionTimeoutError(cell_id, seconds, execution)

        interrupt.cell_timeout_task = owner

    deadline = asyncio.timeout(seconds)
    try:
        if alarm:
            signal.signal(signal.SIGALRM, interrupt)
            signal.setitimer(signal.ITIMER_REAL, seconds)
        async with deadline:
            yield
    except TimeoutError as error:
        if deadline.expired():
            raise CellExecutionTimeoutError(cell_id, seconds, execution) from error
        raise
    finally:
        if alarm:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
            if previous_timer[0]:
                remaining = max(0.000001, previous_timer[0] - (time.monotonic() - started))
                signal.setitimer(signal.ITIMER_REAL, remaining, previous_timer[1])


async def run_cell(code, *, timeout=None, call_id=None):
    """Execute and record code, returning metadata, output, error, status and timing.

    elapsed_seconds measures capture/execution and cleanup with a monotonic clock,
    excluding model requests, ownership waits and session writes. Parent time includes
    nested work. timeout_seconds is the declared budget, not a shorter parent deadline.
    Supports top-level await; trailing expression values are not captured.
    Await nested cells sequentially and finish concurrent I/O before returning.
    A deadline does not supervise child resources or reliably interrupt native code.
    Ordinary execution exceptions become error results; cancellation and storage
    failures propagate. No partial effects are undone. Nested calls inherit a parent,
    not a model call ID; leave call_id unset for direct Python calls.
    timeout is a positive number of seconds, defaulting to the editable cell_timeout.
    Expiry returns an error result; inspect partial effects and child resources before retrying.
    """
    import io
    import traceback
    from contextlib import redirect_stderr, redirect_stdout
    from time import monotonic

    if not isinstance(code, str) or not code.strip():
        raise ValueError("code must be a non-empty string")
    seconds = resolve_cell_timeout(cell_timeout if timeout is None else timeout)

    async with own_cell():
        # A cell may rebind globals; finish its record in the log/context where it started.
        scope = operation.get()
        log = scope["log"] if call_id is not None and scope else session
        active = active_cell
        parent = active.get()
        parent_id = parent[1] if parent and parent[0] is log else None
        cell_id = log.append(
            "cell_start",
            code=code,
            call_id=call_id,
            timeout=seconds,
            **{**record_origin(log), "parent_cell_id": parent_id},
        )
        if call_id is not None and scope and scope.get("call_id") == call_id:
            scope["cell_id"] = cell_id
        execution = object()  # Cell IDs are session-local; deadline ownership is invocation-local.
        token = active.set((log, cell_id))
        # Model-facing output becomes a tool result; display bypasses this capture.
        output = io.StringIO()
        status, error = "completed", None
        started = monotonic()
        try:
            with redirect_stdout(output), redirect_stderr(output):
                async with cell_execution_timeout(seconds, cell_id, execution):
                    await execute(code)
        except BaseException as exc:
            status = (
                "interrupted"
                if not isinstance(exc, Exception)
                or (isinstance(exc, CellExecutionTimeoutError) and exc.execution is not execution)
                else "error"
            )
            error = traceback.format_exc()
            if (
                isinstance(exc, (RecordingError, ModelStopError))
                or not isinstance(exc, Exception)
                or (isinstance(exc, CellExecutionTimeoutError) and exc.execution is not execution)
            ):
                raise
        finally:
            try:
                elapsed_seconds = monotonic() - started
                log.append(
                    "cell_end",
                    cell_id=cell_id,
                    output=output.getvalue(),
                    error=error,
                    status=status,
                    elapsed_seconds=elapsed_seconds,
                )
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
            "timeout_seconds": seconds,
            "elapsed_seconds": elapsed_seconds,
        }


def record_origin(log):
    """Identify the current mode interval and enclosing recorded cell."""
    scope = operation.get()
    mode = next((r for r in reversed(log.records) if r["type"] == "mode"), None)
    active = active_cell.get()
    return {
        "mode": scope["mode"] if scope and scope["log"] is log else (mode["name"] if mode else "normal"),
        "mode_id": scope["mode_id"] if scope and scope["log"] is log else (mode["id"] if mode else None),
        "parent_cell_id": active[1] if active and active[0] is log else None,
    }


def validate_output(output):
    """Reject ambiguous completed envelopes before any effects or projection."""
    if not isinstance(output, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("type"), str) for item in output
    ):
        raise ValueError("Malformed response output")
    seen = set()
    for item in output:
        if item["type"] == "function_call":
            identity = item.get("call_id")
            if not isinstance(identity, str) or not identity or identity in seen:
                raise ValueError("Missing or ambiguous call identity")
            seen.add(identity)
    return seen


# Model access: explicit requests, followed by session-aware recording.
async def request_model(input, **options):
    """Return an SDK response for explicit input, without recording or executing it.

    Current instructions/model/tools supply defaults; options override them.
    History selection and handling returned tool calls belong to the caller.
    """
    from openai import AsyncOpenAI

    global client
    if client is None:
        client = AsyncOpenAI(max_retries=api_retries)
    settings = {
        "parallel_tool_calls": False,
        "store": False,
        **model_options,
        "instructions": "\n\n".join(f"# {name}\n{text}" for name, text in instructions.items()),
        "tools": [cell_tool],
    }
    return await client.responses.create(input=input, **(settings | options))


async def recorded_request(items, *, attempt):
    """Record one logical request and its outcome against the same session.

    SDK transport retries belong to request_model. Response retries and tool
    execution belong to agent_step. Storage failures are not request failures.
    """
    import json
    import traceback

    # Snapshot defaults before awaiting; later live edits belong to future requests.
    requested_model = model_options["model"]
    reasoning = json.loads(json.dumps(model_options.get("reasoning")))
    scope = operation.get()
    log = scope["log"] if scope else session
    origin = record_origin(log)
    request_id = log.append(
        "model_request",
        model=requested_model,
        reasoning=reasoning,
        max_output_tokens=model_options.get("max_output_tokens"),
        tool_choice={"type": "function", "name": "run_cell"},
        parallel_tool_calls=False,
        input_items=len(items),
        attempt=attempt,
        **origin,
    )
    try:
        response = await request_model(
            json.loads(json.dumps(items)),
            tool_choice={"type": "function", "name": "run_cell"},
            parallel_tool_calls=False,
        )
        usage = getattr(response, "usage", None)
        output = [item.model_dump(mode="json", exclude_none=True) for item in response.output]
        status = getattr(response, "status", "completed")
        data = {
            "request_id": request_id,
            "response_id": getattr(response, "id", None),
            "model": requested_model,
            "response_model": getattr(response, "model", None),
            "reasoning": reasoning,
            "status": status,
            "output": output,
            "usage": usage.model_dump(mode="json") if usage is not None else None,
            "error": response.error.model_dump(mode="json") if getattr(response, "error", None) else None,
            "incomplete_details": (
                response.incomplete_details.model_dump(mode="json")
                if getattr(response, "incomplete_details", None)
                else None
            ),
        }
    except BaseException as error:
        log.append(
            "model_request_error",
            request_id=request_id,
            status="error" if isinstance(error, Exception) else "interrupted",
            error=traceback.format_exc(),
        )
        status_code = getattr(error, "status_code", None)
        permanent = (
            isinstance(status_code, int)
            and 400 <= status_code < 500
            and status_code not in {408, 409, 429}
            and getattr(error, "code", None) != "context_length_exceeded"
        )
        # Credentials, permissions and missing models are beyond model repair. Other
        # rejections may come from broken input, so only recovery's own rejection stops.
        if permanent and (status_code in {401, 403, 404} or origin["mode"] == "recovery"):
            raise ModelStopError(f"Permanent model HTTP rejection: {status_code}") from error
        raise
    invalid = None
    try:
        if status == "completed":
            ids = validate_output(output)
            if ids.intersection(pending_calls(log)):
                raise ValueError("Call identity is already pending")
        elif status not in {"incomplete", "failed"}:
            raise ValueError("Malformed response status")
    except ValueError as error:
        invalid = error
    response_id = log.append("model_response", **data, **origin, accepted=status == "completed" and invalid is None)
    if scope is not None:
        scope["response_id"] = response_id
    if invalid:
        raise invalid
    return response


# History projection: shared by normal context and independent recovery.
def records_in_mode(log, start=0, mode=None):
    """Select records by owning mode; mode transitions do not reset context."""
    current = "normal"
    owners = {}
    for record in log.records:
        kind = record["type"]
        if kind == "mode":
            current = record["name"]
        owner = record.get("mode", current)
        reference = (
            record.get("request_id")
            if kind in {"model_response", "model_request_error"}
            else record.get("cell_id")
            if kind == "cell_end"
            else record.get("response_id")
            if kind == "tool_error"
            else None
        )
        if reference in owners:
            owner = owners[reference]
        owners[record["id"]] = owner
        if record["id"] >= start and (mode is None or owner == mode):
            yield record


def project_messages(log, start=0, *, max_output_chars=None, mode=None):
    """Project facts from a record index into API input; synthesize tool replies.

    Source and unlinked cells stay in history. Incomplete model output is omitted.
    Optional shortening affects this view only, not recorded output or errors.
    """
    import copy
    import json

    messages = []
    records = log.records
    for record in records_in_mode(log, start, mode):
        kind = record["type"]
        if kind == "message":
            messages.append(record["message"].copy())
        elif kind == "model_response" and record["status"] == "completed" and record.get("accepted", True):
            messages.extend(item.copy() for item in record["output"])
        elif kind == "turn_end":
            messages.append({"role": "assistant", "content": record["message"]})
        elif kind == "mode" and record.get("content"):
            messages.append({"role": "developer", "content": record["content"]})
        elif kind in ("cell_end", "tool_error"):
            # Only model-linked cells produce tool replies; direct/nested cells remain history.
            call_id = records[record["cell_id"]]["call_id"] if kind == "cell_end" else record["call_id"]
            if call_id is None:
                continue
            fields = ("cell_id", "output", "error", "status") if kind == "cell_end" else ("error",)
            result = {key: record[key] for key in fields}
            if kind == "cell_end":
                result["timeout_seconds"] = records[record["cell_id"]].get("timeout")
                result["elapsed_seconds"] = record.get("elapsed_seconds")
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
    return copy.deepcopy(messages)


def pending_calls(log, *, mode=None):
    """Unanswered calls in the selected mode, linked to started cells."""
    start = 0
    pending = {}
    records = log.records
    for record in records_in_mode(log, start, mode):
        kind = record["type"]
        if kind == "model_response" and record["status"] == "completed" and record.get("accepted", True):
            for item in record["output"]:
                if item.get("type") == "function_call":
                    pending[item["call_id"]] = {"response_id": record["id"], "cell_id": None}
        elif kind == "cell_start" and record["call_id"] in pending:
            pending[record["call_id"]]["cell_id"] = record["id"]
        elif kind == "cell_end":
            pending.pop(records[record["cell_id"]]["call_id"], None)
        elif kind == "tool_error":
            pending.pop(record["call_id"], None)
    return pending


# Context: a bounded model-input view over a complete session log.


def estimate_tokens(value):
    """Conservative UTF-8 heuristic, not a tokenizer."""
    import json

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
                self.log,
                boundary["first_kept"] if boundary else 0,
                max_output_chars=self.max_output_chars,
                mode="normal",
            ),
        ]

    def reset(self, handoff=None, *, cause_id=None):
        """Append a context boundary and return its record ID; do not generate a handoff.

        The next build retains unanswered tool batches so calls/results stay paired.
        No history or live state is removed, and no execution mode is changed.
        """
        if handoff is not None and not isinstance(handoff, str):
            raise TypeError("handoff must be text or None")
        # Reset can run inside a tool: retain its whole response, including sibling calls.
        pending = pending_calls(self.log, mode="normal")
        first_kept = min((p["response_id"] for p in pending.values()), default=len(self.log.records))
        return self.log.append(
            "context_reset", first_kept=first_kept, handoff=handoff, cause_id=cause_id, **record_origin(self.log)
        )

    def estimate(self, items):
        """Estimate input tokens with default instructions/tools and recent recorded usage."""
        estimate = estimate_tokens(
            ["\n\n".join(f"# {name}\n{text}" for name, text in instructions.items()), cell_tool, items]
        )
        # The last measured input plus new messages catches underestimation of provider tokenization.
        normal_ids = {r["id"] for r in records_in_mode(self.log, mode="normal")}
        last = next(
            (
                r
                for r in reversed(self.log.records)
                if r["type"] == "context_reset"
                or (r["type"] == "model_response" and r.get("usage") and r["id"] in normal_ids)
            ),
            None,
        )
        import math

        measured = (
            last.get("usage", {}).get("input_tokens")
            if last and last["type"] == "model_response" and isinstance(last.get("usage"), dict)
            else None
        )
        if (
            isinstance(measured, (int, float))
            and not isinstance(measured, bool)
            and math.isfinite(measured)
            and measured >= 0
        ):
            estimate = max(
                estimate,
                measured
                + estimate_tokens(
                    project_messages(self.log, last["id"], max_output_chars=self.max_output_chars, mode="normal")
                ),
            )
        return estimate


# Tool completion: close interrupted calls without replaying their effects.
def repair_pending(log=None, response_id=None):
    """Repair only the ending batch; startup may repair all historical work."""
    scope = operation.get()
    log = log if log is not None else (scope["log"] if scope else session)
    if response_id is None and scope:
        response_id = scope.get("response_id")
        if response_id is None:
            return
    active = active_cell.get()
    live = set()
    records = log.records
    while active and active[0] is log:
        live.add(active[1])
        parent = records[active[1]]["parent_cell_id"]
        active = (log, parent) if parent is not None else None
    for call_id, pending in pending_calls(log).items():
        if pending["cell_id"] in live or (response_id is not None and pending["response_id"] != response_id):
            continue
        error = "Execution interrupted; effects unknown. Inspect before retrying."
        if pending["cell_id"] is None:
            log.append("tool_error", call_id=call_id, response_id=pending["response_id"], error=error)
        else:
            log.append("cell_end", cell_id=pending["cell_id"], status="interrupted", output="", error=error)


class ModelStopError(RuntimeError):
    """Stop the current turn without recovery: refusal, unrepairable rejection or repeated failure."""


def end_turn(message):
    """Request completion of this directly dispatched model cell's invocation.

    Call once, as the cell's last action. The nonblank final message is recorded and
    displayed only after this cell succeeds. Errors/cancellation discard the intent.
    Recovery and nested model steps end only themselves; the REPL stays alive.
    """
    scope = operation.get()
    active = active_cell.get()
    if (
        not scope
        or not active
        or active[0] is not scope["log"]
        or scope.get("call_id") is None
        or active[1] != scope.get("cell_id")
    ):
        raise RuntimeError("end_turn requires a directly dispatched model cell")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("end_turn requires a nonblank message")
    if scope.get("end_message") is not None:
        raise ValueError("end_turn may be requested only once per cell")
    scope["end_message"] = message


async def agent_step(items):
    """Require tool actions; complete only after a successful cell calls end_turn.

    Three response attempts cover transient failures and no-call continuations.
    Refusal stops explicitly. Exhausted or non-retryable failures enter recovery;
    in recovery, a non-retryable failure stops instead of cycling through fallback.
    Never replay executed effects; end_turn commits only after cell-end recording.
    """
    import asyncio
    import json

    inherited = operation.get()
    log = inherited["log"] if inherited else session
    scope = {"log": log, **record_origin(log), "response_id": None}
    token = operation.set(scope)
    try:
        for attempt in range(3):
            response = await recorded_request(items, attempt=attempt + 1)
            status = getattr(response, "status", "completed")
            if status == "completed":
                refused = False
                for item in response.output:
                    if item.type == "message":
                        for part in item.content:
                            if part.type in {"output_text", "refusal"}:
                                text = part.text if part.type == "output_text" else part.refusal
                                print(text, file=console, flush=True)
                                refused |= part.type == "refusal"
                if refused:
                    log.append("model_stop", response_id=scope["response_id"], reason="refusal")
                    raise ModelStopError("Model refused the request")
                calls = [item for item in response.output if item.type == "function_call"]
                if calls:
                    break
                reminder = {
                    "role": "developer",
                    "content": "No tool call was returned. Continue through run_cell. To finish this invocation, call end_turn(message) in a successful cell; ordinary text does not end it.",
                }
                # Preserve complete provider items for continuation, not incomplete output.
                items = [*items, *json.loads(json.dumps(log.records[scope["response_id"]]["output"])), reminder]
                code, reason, retry_reason = None, "No tool call was returned", "no_tool_calls"
                retryable = True
            else:
                error = getattr(response, "error", None)
                code = getattr(error, "code", None)
                details = getattr(response, "incomplete_details", None)
                reason = getattr(error, "message", None) or getattr(details, "reason", None)
                retry_reason = status
                retryable = status == "incomplete" or (
                    status == "failed"
                    and code in {None, "server_error", "rate_limit_exceeded", "timeout", "overloaded"}
                )
            failure = f"Model response {status} ({code or 'unspecified'}): {reason or 'No details'}"
            if not retryable and scope["mode"] == "recovery":
                log.append("model_stop", response_id=scope["response_id"], reason="permanent_failure")
                raise ModelStopError(failure)
            if not retryable or attempt == 2:
                raise RuntimeError(failure)
            delay = 2**attempt if status == "failed" else 0
            log.append(
                "model_retry",
                response_id=getattr(response, "id", None),
                attempt=attempt + 2,
                reason=retry_reason,
                error_code=code,
                delay_seconds=delay,
            )
            if delay:
                await asyncio.sleep(delay)
        for index, call in enumerate(calls):
            try:
                if call.name != "run_cell":
                    raise ValueError(f"Unknown tool: {call.name}")
                arguments = json.loads(call.arguments)
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must be an object")
                if set(arguments) - {"code", "timeout"}:
                    raise ValueError("arguments may contain only code and timeout")
                code = arguments["code"]
                if not isinstance(code, str) or not code.strip():
                    raise ValueError("code must be a non-empty string")
                timeout = arguments.get("timeout")
                timeout = resolve_cell_timeout(cell_timeout if timeout is None else timeout)
            except (ValueError, KeyError, TypeError) as error:
                log.append("tool_error", call_id=call.call_id, response_id=scope["response_id"], error=str(error))
                continue
            scope.update(call_id=call.call_id, cell_id=None, end_message=None)
            result = await run_cell(code, timeout=timeout, call_id=call.call_id)
            message = scope["end_message"]
            scope.update(call_id=None, cell_id=None, end_message=None)
            if result["status"] == "completed" and message is not None:
                for skipped in calls[index + 1 :]:
                    log.append(
                        "tool_error",
                        call_id=skipped.call_id,
                        response_id=scope["response_id"],
                        error="Not executed: end_turn completed this invocation.",
                    )
                log.append(
                    "turn_end",
                    cell_id=result["cell_id"],
                    response_id=scope["response_id"],
                    message=message,
                    **record_origin(log),
                )
                print(message, file=console, flush=True)
                return "completed"
        return "continue"
    finally:
        try:
            if not log.broken and scope["response_id"] is not None:
                repair_pending(log, scope["response_id"])
        finally:
            operation.reset(token)


# Normal work: context selection and budget checks around the shared agent step.
async def agent_loop():
    """Let the agent manage context; overflow falls back to recovery."""
    global context
    while True:
        if getattr(context, "log", session) is not session:
            context = Context(session)
        items = context.build()
        tokens = context.estimate(items)
        budget = context_window - model_options["max_output_tokens"]
        if budget <= 0 or tokens > budget:
            raise RuntimeError(
                "Context exceeds the input budget. Start fresh context with context.reset(handoff=...) before continuing work."
            )
        if tokens >= budget * 0.8:
            warning = {
                "role": "developer",
                "content": "Context is near its input budget. Write a concise handoff and call context.reset(handoff=...) when appropriate; no automatic summary is generated.",
            }
            if tokens + estimate_tokens([warning]) > budget:
                raise RuntimeError("Context including guidance exceeds the input budget")
            items.append(warning)
        if await agent_step(items) != "continue":
            return


# Recovery: independent context, shared agent step, and a local fallback.
async def recover(reason):
    """Repair independently, resetting normal context if recovery fails.

    Return "completed" or "failed" (after the fresh-context fallback) when normal work
    may resume, not as proof of repair. Cancellation and fallback failures propagate.
    Still depends on execution, model access and storage.
    """
    import traceback

    global context
    log = session
    start = len(log.records)
    note = (
        f"Recovery mode. Full failure: session.records[{start}]['reason']. "
        f"Reason (may be truncated): {reason[-1000:]}\n"
        f"You share the same live REPL and session. Original task and progress are in session.records[:{start}]. "
        "task_state and other live variables survive the mode change; inspect relevant selections with print as needed. "
        "Read history in small chunks and inspect current state before acting. Repair or roll back the fault; "
        "keep the core capabilities unchanged. Do not replay completed effects or execute the original task here. "
        "Do not invoke the work loop during repair. The main context is paused, not reset. "
        "Change it or leave continuation information only as needed for the repair. "
        "Verify what you can, then call end_turn(message) in a successful cell when ready to resume. "
        "The agent will try the repaired work loop next."
    )
    log.append("mode", name="recovery", reason=reason, content=note)
    token = operation.set({"log": log, "mode": "recovery", "mode_id": start})
    status = "failed"
    try:
        recovery_error = None
        try:
            while True:
                # Bypass the editable Context policy; keep the repair instruction last.
                items = project_messages(log, start + 1, mode="recovery")
                status = "failed"
                status = await agent_step([*items, {"role": "developer", "content": note}])
                if status != "continue":
                    break
        except (RecordingError, CellExecutionTimeoutError, ModelStopError):
            raise
        except Exception:
            recovery_error = traceback.format_exc()
        if status != "completed":
            failure = log.append(
                "recovery_error",
                recovery=start,
                reason=reason,
                recovery_error=recovery_error or "Recovery did not complete.",
            )
            # Rebuild rather than call a possibly damaged Context instance.
            context = Context(session)
            context.reset(
                handoff=(
                    f"Recovery failed; use this fresh context to repair and continue the original task. "
                    f"Failure details: origin session {log.records[0]['session_id']} (path {log.path}), record {failure}. "
                    f"Prior task and progress precede record {start} in that session. "
                    "The REPL and external effects are unchanged. Inspect history and live state before acting; "
                    "do not repeat completed effects. Repair what is needed, then continue the user's task."
                ),
                cause_id=failure if session is log else None,
            )
    finally:
        try:
            if not log.broken:
                log.append("mode", name="normal", recovery=start, status=status)
        finally:
            operation.reset(token)
    if getattr(context, "log", session) is not session:
        context = Context(session)
    return status


# Local tools: ordinary callables for cell code, separate from execution/recording policy.
# Built-in helpers live here; the catalog can also describe functions defined in cells or loaded files.
# Capture the console before any run_cell redirects output.
console = sys.stdout


async def run_process(command, *, timeout=None, cwd=None, env=None):
    """Run a POSIX argument vector with bounded process group cleanup and retained output.

    The running command's budget defaults to cell_timeout. An enclosing cell deadline
    still cancels it, cleaning up the group. TimeoutExpired/CalledProcessError retain stdout and
    stderr; normal output is returned for explicit inspection. Cancellation stops
    the owned group before propagating. Each terminate/kill wait allows one second.
    Spawn can still be delayed by the OS; escaped groups and uninterruptible kernel
    work cannot be supervised here. Use this for bounded jobs, not background services.
    """
    import asyncio
    import os
    import signal
    import subprocess
    import sys
    from tempfile import TemporaryFile

    seconds = resolve_cell_timeout(cell_timeout if timeout is None else timeout)
    if (
        not isinstance(command, (list, tuple))
        or not command
        or any(not isinstance(arg, (str, os.PathLike)) for arg in command)
    ):
        raise ValueError("Process command must be a nonempty argument list; invoke a shell explicitly if needed")
    if os.name != "posix":
        raise NotImplementedError("Managed process groups require POSIX")

    failure = None

    async def finish(task):
        # Cancellation can arrive during spawn or cleanup. Finish ownership transfer
        # and bounded cleanup before propagating it; do not orphan the child.
        nonlocal failure
        while True:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError as error:
                if task.cancelled():
                    raise
                failure = error

    # Files avoid pipe backpressure and EOF waits on descriptors inherited by children.
    with TemporaryFile() as stdout_file, TemporaryFile() as stderr_file:
        start = asyncio.create_task(
            asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout_file,
                stderr=stderr_file,
                start_new_session=True,
            )
        )
        process = None
        deadline = asyncio.timeout(seconds)
        try:
            async with deadline:
                process = await asyncio.shield(start)
                await process.wait()
        except BaseException as error:
            failure = error
        if process is None:
            try:
                process = await finish(start)
            except BaseException:
                if failure is not None:
                    raise failure
                raise

        async def stop_process_group():
            def send(signum):
                try:
                    os.killpg(process.pid, signum)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    # Darwin can return EPERM while an emptied orphan group is
                    # disappearing. Confirm absence; genuine permission failures
                    # must still be reported as unconfirmed cleanup.
                    if sys.platform != "darwin":
                        raise
                    check = subprocess.run(["ps", "-o", "pid=", "-g", str(process.pid)], capture_output=True, timeout=1)
                    if check.returncode not in (0, 1) or check.stdout.strip():
                        raise

            try:
                send(signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), 1)
                except TimeoutError:
                    pass
            finally:
                # Also remove descendants left behind when the command exits normally.
                send(signal.SIGKILL)
            await asyncio.wait_for(process.wait(), 1)

        cleanup_error = None
        try:
            await finish(asyncio.create_task(stop_process_group()))
        except Exception as error:
            cleanup_error = error
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout = stdout_file.read().decode("utf-8", errors="replace")
        stderr = stderr_file.read().decode("utf-8", errors="replace")

    if isinstance(failure, TimeoutError) and deadline.expired():
        failure = subprocess.TimeoutExpired(command, seconds, output=stdout, stderr=stderr)
    if failure is None:
        if cleanup_error is not None:
            failure = RuntimeError("Managed process cleanup failed; process group termination is unconfirmed")
        elif process.returncode:
            failure = subprocess.CalledProcessError(process.returncode, command, output=stdout, stderr=stderr)
    if failure is not None:
        if cleanup_error is not None:
            failure.add_note(f"Process group cleanup failed: {cleanup_error!r}")
        for name, value in (("stdout", stdout), ("stderr", stderr)):
            if value:
                failure.add_note(f"Process {name}:\n{value}")
        raise failure
    return {"returncode": process.returncode, "stdout": stdout, "stderr": stderr}


def display(*values, sep=" ", end="\n", flush=True):
    """Render values for the user, separately from captured model observations.

    sep/end/flush follow print; returns None. The rendered text is recorded in the
    active cell's session (or current session outside a cell) and bypasses capture.
    Use before native input() for questions; print separately if the model also
    needs the value. Recording failures propagate before presentation.
    """
    import io

    active = active_cell.get()
    log, cell_id = active if active else (session, None)
    rendered = io.StringIO()
    print(*values, sep=sep, end=end, file=rendered)
    text = rendered.getvalue()
    log.append("display", cell_id=cell_id, text=text)
    print(text, end="", file=console, flush=flush)


# Plain name-to-purpose index, maintained by the agent under the prompt's tool usage policy.
# Keep detailed contracts beside each callable; native input uses Python's help(input).
tool_catalog = {
    "end_turn": "End this invocation after a successful model cell, recording and displaying its final message.",
    "run_process": "Run a bounded POSIX command and return output after owned process-group cleanup.",
    "display": "Present values to the user and record them separately from model observations.",
    "input": "Read a line synchronously; raises EOFError at end of input. Present questions with display first.",
}


# Conversation entry: user input, mode entry and client lifetime.
async def run_agent(user_input=None, *, interactive=False):
    """Resume normal work after recovery or reset; each new loop failure starts fresh recovery."""
    import traceback

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
                    failed_repairs = 0
                    while True:
                        try:
                            await agent_loop()
                            break
                        except (RecordingError, CellExecutionTimeoutError, ModelStopError):
                            raise
                        except Exception as error:
                            # Fallback work may re-enter recovery, but two repairs in a row that fell
                            # back end the turn on the next failure instead of cycling.
                            if failed_repairs >= 2:
                                session.append(
                                    "model_stop", reason="repeated_recovery_failure", error=traceback.format_exc()
                                )
                                raise ModelStopError("Work failed again after two failed recoveries") from error
                            failed = await recover(traceback.format_exc()) == "failed"
                            failed_repairs = failed_repairs + 1 if failed else 0
                except ModelStopError as error:
                    # Interactive work keeps the live environment and waits for new input.
                    if not interactive:
                        raise
                    print(f"Stopped: {error}", file=console, flush=True)
            if not interactive:
                return
            user_input = None
    finally:
        if client is not None:
            await client.close()
            client = None


# Initialize agent state once, after all definitions and local tools are available.
session_path = os.getenv("FIBONA_SESSION", str(Path.home() / ".local/state/fibona/sessions" / f"{uuid4().hex}.jsonl"))
session = Session(session_path)
task_state = {}  # Agent-maintained notes, not automatic summaries; a new process starts empty.
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
session.append("source", code=In[-1])  # noqa: F821 -- agent snapshot from native REPL history.
client = None
await run_agent(interactive=True)  # noqa: F704
