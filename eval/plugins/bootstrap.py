"""Evaluation bootstrap with working context and an optional minimal ReAct loop.

agent.py writes this file into the task container. It runs as a cell, so argv and call_me come from the
session. The task comes from the command line; nobody answers input(), so the agent ends the run with quit().
"""

import json

from bootstraps.terminal.context import initialize_context


def run_react(messages, *, tools=(), handlers=None, max_turns=64):
    """Update messages in place; return a final Response, or None at the turn limit."""
    handlers = handlers or {}
    for _ in range(max_turns):
        response = globals()["call_llm_api"](
            input=list(messages),
            reasoning={"effort": globals()["model_reasoning_effort"]},
            store=False,
            include=["reasoning.encrypted_content"],
            **({"tools": list(tools), "parallel_tool_calls": False} if tools else {}),
        )
        if response.status != "completed":
            raise RuntimeError(f"ReAct response status: {response.status}")
        messages.extend(response.output)
        calls = [item for item in response.output if item.type == "function_call"]
        if not calls:
            return response
        for call in calls:
            try:
                output = handlers[call.name](**json.loads(call.arguments))
                if not isinstance(output, str | list):
                    output = json.dumps(output)
            except Exception as error:
                output = json.dumps({"error": str(error)})
            messages.append({"type": "function_call_output", "call_id": call.call_id, "output": output})
    return None


instructions = """
You are running unattended in a benchmark container: nobody reads your output or answers input().
Before preparing final deliverables or declaring completion, reread startup_task in full and check every required output, path, format, behavior, and constraint against it.
Verify delivery by inspecting the actual files, database records, or service state and the results of relevant checks; a plan, summary, or successful file write alone does not establish completion.
Submit only one set_next_cell per wake; it schedules future work, which has not executed until recorded in history.
If a required deliverable is missing or a check fails, keep it pending and continue working. Call quit() only after verifying the task's requirements are satisfied.
These cells use Fibona's isolated Python runtime, which can differ from the task's Python.
Inspect the task's supplied interpreters and virtualenvs for preinstalled dependencies before downloading duplicate packages into this runtime.
The uv executable is available on PATH at $HOME/.local/bin/uv.
Stay within the task's declared memory budget, shared by Fibona, session objects, and child processes; leave headroom for the interpreter and SDK.
Inspect large objects using types, lengths, shapes, and small samples. Do not stringify entire globals(), history, model responses, or large objects before truncating them: repr(value)[:N] and str(value)[:N] still construct the full string first.
Write large results to files and keep paths and compact summaries in session state; avoid repeatedly embedding earlier history or output into new output.
For file discovery, start with paths explicitly provided by the task and the current working directory, then gradually widen the search.
Avoid unbounded recursive searches from the filesystem root; set time, depth, and result-count limits, do not follow directory symlinks, skip /proc, /sys, /dev, and read only regular files.
Every search command must have an explicit wall-clock timeout (for example, timeout 30s find ... or subprocess.run(..., timeout=30)); run potentially blocking Python searches in a subprocess with a timeout too.
If a search times out, narrow its scope or change methods.
Run programs expected to take a long time in separate processes, save their logs, and check progress periodically; avoid waiting indefinitely in a single cell.
For a task needing repeated inspection, implementation, testing, or repair, default to run_react(messages, tools=schemas, handlers=functions, max_turns=64) inside one cell instead of doing each step through call_me.
After any essential initial inspection, define the minimal tools and invoke the loop in that same cell; do not repeatedly postpone it for more planning or environment inspection. Environment discovery and dependency setup can themselves be delegated through those tools.
It has no default tools or task prompt: supply task messages, Responses API function schemas, and a name-to-synchronous-callable mapping. Develop only the tools needed; do not expose call_me or quit as inner tools.
The loop uses the configured model and effective reasoning effort, preserves reasoning and tool observations in messages, and returns a Response when the model replies without tool calls, or None at the turn limit. Reuse messages to continue if needed.
Keep direct Python for simple deterministic work. Verify the loop's actual results yourself, then call_me with concise evidence to continue or quit when the original task is verified complete.
""".strip()
task = " ".join(argv)  # noqa: F821 -- fibona.py binds the arguments it did not consume.
build_context = initialize_context(globals())
call_me(  # noqa: F821 -- core binds call_me.
    input=[
        *build_context(),
        {
            "role": "user",
            "content": "Start the user's task. If it requires iterative actions and observations, submit a cell that develops the minimal tools, supplies the original task and relevant evidence in messages, and actually invokes run_react. Use direct Python for simple deterministic work.",
        },
    ],
)
