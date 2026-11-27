"""Evaluation bootstrap with working context and an optional minimal ReAct loop.

agent.py writes this file into the task container. It runs as a cell, so argv and call_me come from the
session. The task comes from the command line; nobody answers input(), so the agent ends the run with quit().
"""

import inspect
import json

from core import agent as core_agent
from core import env as core_env


def run_react(messages, *, tools=(), handlers=None, max_turns=64):
    """Update messages in place; return a final Response, or None at the turn limit."""
    handlers = handlers or {}
    for _ in range(max_turns):
        response = globals()["call_llm_api"](
            input=list(messages),
            instructions=globals()["model_capabilities_prompt"],
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
Use a short plan for complex work and update it as facts change. Try another approach when blocked; do not restart successful work or generate alternative solutions without a concrete unresolved issue.
Before preparing final deliverables or declaring completion, reread startup_task in full and check every required output, path, format, behavior, and constraint against it.
Verify delivery by inspecting the actual files, database records, or service state and the results of relevant checks; a plan, summary, or successful file write alone does not establish completion.
Run tests appropriate to the change and complete required checks. Once those pass, broaden or repeat testing only when new changes, failures, or unresolved concerns justify it; otherwise, continue toward completing the task.
Submit only one set_next_cell per wake; it schedules future work, which has not executed until recorded in history.
If a required deliverable is missing or a check fails, keep it pending and continue working. Call quit() only after verifying the task's requirements are satisfied.
When the task is verified complete, report what changed, why, how it was tested, and any material risks or limitations, then call quit(); do not keep reviewing or repeat external submissions.
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
Keep direct Python for simple deterministic work. Continue the same ReAct conversation while
the task has unresolved issues; preserve its observations, decisions, and verified results.
Use the loop's deliverables and recorded verification evidence to check completion against the original task;
its return does not by itself require a separate review pass. Continue the existing conversation for
failed or missing required checks, carrying the evidence and preserving the original objective.
Quit when the original task is verified complete.
""".strip()
task = " ".join(argv)  # noqa: F821 -- fibona.py binds the arguments it did not consume.
startup_task = task
task_state = {"goal": startup_task, "constraints": [], "completed": [], "pending": []}
_context_instructions = """
## Context

Keep the initialized `context`: its developer instructions and original task retain their roles and text.
Before every `call_me(input=context)`, append unsynced `history` code, output, and errors, including the
current cell's results so far. Use assistant messages for code and user messages labeled "Execution
observation" for results. Track cell indices and output offsets; append only new data, even after compaction.
Append relevant decisions and pending work as assistant notes. Only rewrite working history for compaction;
never replace the original task with a review prompt. After an error/recovery wake, inspect the failure, sync history, and resume
with the same context and sync state. A failed cell does not reset the task or imply its actions failed.

Reuse LLM prompt caches to reduce cost: keep existing messages unchanged and append at the end.
Keep large data in variables/files and print targeted results with references rather than dumping everything.

Before reaching the context window limit, leaving room for output, compact context yourself. Keep `context[:2]` unchanged and replace older working history with an assistant
summary of verified facts, evidence references, decisions, completed actions, pending work, and uncertainties.
Retain recent results needed for the next step. Keep full history in `history` and preserve sync indices and
output offsets so summarized entries are not appended again. Resume appending to reuse the new cached prefix.
""".strip()
instructions += "\n\n" + _context_instructions
context = [
    {"role": "developer", "content": instructions},
    {"role": "user", "content": f"Startup task: {startup_task}"},
]
source_code = {
    "core/agent.py": inspect.getsource(core_agent),
    "core/env.py": inspect.getsource(core_env),
    "eval/plugins/bootstrap.py": history[-1]["code"],  # noqa: F821
}
var_docs = {
    "startup_task": "Original user task.",
    "context": "Persistent working context; pass explicitly to call_me().",
    "task_state": "Current goal, constraints, completed work and pending work.",
}
call_me(  # noqa: F821 -- core binds call_me.
    input=[
        *context,
        {
            "role": "user",
            "content": "Start the user's task. If it requires iterative actions and observations, submit a cell that develops the minimal tools, supplies the original task and relevant evidence in messages, and actually invokes run_react. Use direct Python for simple deterministic work.",
        },
    ],
)
