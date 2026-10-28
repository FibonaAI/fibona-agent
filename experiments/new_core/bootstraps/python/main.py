"""Cell 0: the complete editable agent. Executed as source, never imported by the host."""

import asyncio
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
# SDK transport retries only; agent_step has a separate, fixed response-attempt limit.
api_retries = int(os.getenv("FIBONA_API_RETRIES", "2"))
if api_retries < 0:
    raise ValueError("FIBONA_API_RETRIES must not be negative")


# Each section owns one concern. Keep mechanisms in code and durable guidance here.
# request_model composes the current values; edits affect future requests, not active ones.
instructions = {
    "identity": """
You are Fibona, a self-modifying agent that evolves through learning, feedback and verified changes.
Understand and improve your ways of working to better accomplish the user's goals.
""".strip(),
    "working": """
## Advance the task

Establish the goal, constraints and evidence of completion. Work independently while useful progress
is possible. Inspect, act and verify; distinguish assumptions, intentions, attempts and verified outcomes.
At key steps, compare expected and actual results. When they differ or a required step fails, stop
dependent work and inspect existing observations and current state before continuing or repeating an
action. Let the evidence change your understanding and plan.
Ask when missing information or a decision blocks progress; report unfinished work and uncertainty.
In recovery mode, repair the fault and hand back to work; a handoff alone does not prove repair.
""".strip(),
    "environment": """
## Use and maintain your environment

You operate in a persistent Python REPL. Your instructions, tools, context policy and work loop,
along with the variables, functions and resources you create, form a working environment you can
inspect, maintain and modify.

Starting points:
- `instructions`: your current instructions; changes affect subsequent requests.
- `context`: organize context for subsequent model requests.
- `session`: append-only records of source, model responses and execution history.
- `run_cell`: execute code; the only capability exposed as a model tool.
- `request_model`: request a model response from code.

Discover interface details from relevant code and documentation. Source, execution history and
current bindings may disagree, and failures can leave partial changes. Use them together with actual
behavior to understand the current environment.

The agent defined in cell 0 and later task cells share one live global namespace.
Existing functions may read these global bindings when called; a top-level import,
assignment or definition can therefore change their behavior even if that cell succeeds.
For ordinary task work, prefer local scopes and keep persistent task data under a
distinct name, checking that it is not already in use. Before replacing shared bindings
or mutating the objects they refer to, inspect their current values and dependent code,
then follow the self-modification checks below. Preserve session recording, model access
and cell execution unless intentionally changing them.

Make the purpose, dependencies and failure behavior of reusable variables, functions and tools
discoverable. Manage temporary objects, files, processes and connections too; release or clean them
up when no longer needed so they do not keep consuming resources. Check dependencies and preserve
useful work before cleanup; do not blindly clear shared state.

Maintain context suited to the next step, keeping necessary goals, constraints, progress, open
questions and evidence pointers; retrieve details as needed. When changing direction, pausing or
reorganizing context, leave a handoff sufficient to continue. Account for retrieval and recovery
costs when reorganizing, and retain material that still needs direct inspection.

Context changes do not reset the REPL or undo external actions; process restarts do not automatically
restore in-memory objects. Check relevant current state when resuming to avoid repeating actions.
Explicitly persist state and experience that must last, with pointers for retrieval. Keep session
records append-only rather than rewriting historical facts.

Cells share state and support top-level await; trailing expression values are not captured.
Await nested cells sequentially, never concurrently, and finish concurrent I/O before returning.
Failure, retries and code rollback do not automatically undo existing external effects.

Model output and user presentation are separate channels. `print(...)` writes output that is recorded
and returned to the model; `display(...)` presents to the user without automatically entering model
context. Write a value to both channels if both audiences need it. Model reply text is recorded but
not automatically shown to the user. Use `display(...)` for progress, questions before input(),
and the final answer before ending the turn.
""".strip(),
    "reflection": """
## Reflect and iterate

Notice how your ways of working and environment affect results. Treat user corrections, conflicts
with expectations, repeated mistakes, inefficient patterns and resource problems as signals to
reflect; also notice effective approaches worth reusing.

Use history, current state and implementation to investigate causes, distinguishing evidence from
explanation. When evidence is insufficient, use a small check that can distinguish possible causes.
Based on the cause, decide whether to revise your understanding and approach or change tools, state
organization, context policy, instructions or the work loop. Use existing capabilities when sufficient;
otherwise prefer the smallest coherent change. Adding rules is not the default remedy.
Self-modification must preserve the user's constraints and evidence of success.

Before a change, identify the behavior to improve and the evidence for judging improvement. Inspect
affected callers, bindings and mutable state; preserve working definitions and necessary state.
Saved references are not state snapshots. Replacing a definition neither isolates its dependencies
nor changes active invocations. Keep session storage, model requests and cell execution stable by default.
Switch, check the actual usage path including affected callers, and roll back on failure within the
same cell, without another model request or reliance on the changed capability. For effects that
require later evaluation, retain a rollback path and track what remains unverified.

Apply verified lessons to subsequent plans or tool checks, preserving their conditions and evidence
pointers. Check whether they improve results and what costs they add, then retain, revise or revert
them. Do not turn a single outcome into a permanent rule.
Keep durable guidance in prompt sections, mechanisms and interfaces in code/docstrings, and task
progress in working state or history. Prefer discovery pointers over duplicated implementation details;
check the composed prompt for contradictions after edits and remove guidance that no longer applies.
""".strip(),
}


# Session storage: append-only facts and read-only history views.
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


def record_origin(log):
    """Identify the current mode interval and enclosing recorded cell."""
    mode = next((r for r in reversed(log.records) if r["type"] == "mode"), None)
    active = active_cell.get()
    return {
        "mode": mode["name"] if mode else "normal",
        "mode_id": mode["id"] if mode else None,
        "parent_cell_id": active[1] if active and active[0] is log else None,
    }


# Model access: explicit requests, followed by session-aware recording.
async def request_model(input, **options):
    """Return an SDK response for explicit input, without recording or executing it.

    Current instructions/model/tools supply defaults; options override them.
    History selection and handling returned tool calls belong to the caller.
    """
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
    # Snapshot defaults before awaiting; later live edits belong to future requests.
    requested_model = model_options["model"]
    reasoning = json.loads(json.dumps(model_options.get("reasoning")))
    log = session
    request_id = log.append(
        "model_request",
        model=requested_model,
        reasoning=reasoning,
        max_output_tokens=model_options.get("max_output_tokens"),
        input_items=len(items),
        attempt=attempt,
        **record_origin(log),
    )
    try:
        response = await request_model(items)
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
        raise
    # Storage failures propagate; they are not model request failures.
    log.append("model_response", **data)
    return response


# History projection: shared by normal context and independent recovery.
def records_in_mode(log, start=0, mode=None):
    """Select a view of the linear log; mode transitions do not reset context."""
    current = "normal"
    for record in log.records:
        if record["type"] == "mode":
            current = record["name"]
        if record["id"] >= start and (mode is None or current == mode):
            yield record


def project_messages(log, start=0, *, max_output_chars=None, mode=None):
    """Project facts from a record index into API input; synthesize tool replies.

    Source and unlinked cells stay in history. Incomplete model output is omitted.
    Optional shortening affects this view only, not recorded output or errors.
    """
    messages = []
    for record in records_in_mode(log, start, mode):
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


def pending_calls(log, *, mode=None):
    """Unanswered calls in the selected mode, linked to started cells."""
    start = 0
    pending = {}
    for record in records_in_mode(log, start, mode):
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
        if last and last["type"] == "model_response":
            estimate = max(
                estimate,
                last["usage"]["input_tokens"]
                + estimate_tokens(
                    project_messages(self.log, last["id"], max_output_chars=self.max_output_chars, mode="normal")
                ),
            )
        return estimate


# Tool completion: close interrupted calls without replaying their effects.
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


# Agent step: retry unsuccessful responses, then dispatch complete tool calls.
async def agent_step(items):
    """Request a recorded response, dispatch complete tool calls, and return a loop status.

    Make at most three response attempts, independently of SDK transport retries.
    Never replay partial output. Return 'continue' after tools or 'completed' after
    a final reply. Other response outcomes raise for recovery after retries.
    """
    try:
        log = session
        for attempt in range(3):
            response = await recorded_request(items, attempt=attempt + 1)
            status = getattr(response, "status", "completed")
            if status == "completed":
                break
            error = getattr(response, "error", None)
            code = getattr(error, "code", None)
            details = getattr(response, "incomplete_details", None)
            reason = getattr(error, "message", None) or getattr(details, "reason", None)
            retryable = status == "incomplete" or (
                status == "failed" and code in {None, "server_error", "rate_limit_exceeded", "timeout", "overloaded"}
            )
            if not retryable or attempt == 2:
                raise RuntimeError(f"Model response {status} ({code or 'unspecified'}): {reason or 'No details'}")
            # Retry only the request. Partial output is recorded but never executed or replayed.
            delay = 2**attempt if status == "failed" else 0
            log.append(
                "model_retry",
                response_id=getattr(response, "id", None),
                attempt=attempt + 2,
                reason=status,
                error_code=code,
                delay_seconds=delay,
            )
            if delay:
                await asyncio.sleep(delay)
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


# Normal work: context selection and budget checks around the shared agent step.
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


# Recovery: independent context, shared agent step, and a local fallback.
async def recover(reason):
    """Repair independently, resetting normal context if recovery fails.

    Return when normal work may resume, not as proof of repair. Cancellation and
    fallback failures propagate. Still depends on execution, model access and storage.
    """
    global context
    start = len(session.records)
    note = (
        f"Recovery mode. Full failure: session.records[{start}]['reason']. "
        f"Reason (may be truncated): {reason[-1000:]}\n"
        f"You share the same live REPL and session. Original task and progress are in session.records[:{start}]. "
        "Read history in small chunks and inspect current state before acting. Repair or roll back the fault; "
        "keep the core capabilities unchanged. Do not replay completed effects or execute the original task here. "
        "Do not invoke the work loop during repair. The main context is paused, not reset. "
        "Change it or leave continuation information only as needed for the repair. "
        "Verify what you can, then finish your response when ready to resume. "
        "The bootstrap will try the repaired work loop next."
    )
    session.append("mode", name="recovery", reason=reason, content=note)
    status = "failed"
    try:
        recovery_error = None
        try:
            while True:
                # Bypass the editable Context policy; keep the repair instruction last.
                items = project_messages(session, start + 1)
                status = "failed"
                status = await agent_step([*items, {"role": "developer", "content": note}])
                if status != "continue":
                    break
        except Exception:
            recovery_error = traceback.format_exc()
        if status != "completed":
            failure = session.append(
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
                    f"Failure details: session.records[{failure}]. Prior task and progress: session.records[:{start}]. "
                    "The REPL and external effects are unchanged. Inspect history and live state before acting; "
                    "do not repeat completed effects. Repair what is needed, then continue the user's task."
                ),
                cause_id=failure,
            )
    finally:
        session.append("mode", name="normal", recovery=start, status=status)


# Conversation entry: user input, mode entry and client lifetime.
async def run_agent(user_input=None, *, interactive=False):
    """Resume normal work after recovery or reset; each new loop failure starts fresh recovery."""
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
                while True:
                    try:
                        await agent_loop()
                        break
                    except Exception:
                        await recover(traceback.format_exc())
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
