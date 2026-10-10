"""Session initialization and first model call, executed in the core's first cell.

The cell sets task. Core binds history and call_me before execution.
"""

import inspect as _inspect
import types as _types
from pathlib import Path as _Path

from core import agent as _core_agent
from core import env as _core_env

# The user task is supplied by runtime.py.
task = globals()["task"]
startup_task = task  # Preserve the runtime-supplied task when the current goal changes.
task_state = {
    "goal": startup_task,
    "constraints": [],
    "completed": [],
    "pending": [],
}
source_code = {
    "core/agent.py": _inspect.getsource(_core_agent),
    "core/env.py": _inspect.getsource(_core_env),
}

instructions = """
Your working instructions, variables() and build_context() are Python state in this session.
You can inspect their initial implementation in history[0]['code'] and change them in later cells.
source_code contains the core runtime source. Read relevant excerpts and include them in input when needed.
Use call_me(input=[*build_context(), {"role": "user", "content": observations}]) to send these instructions and your selected context.
Keep useful data and task-specific helpers in session variables; update var_docs to describe them.
startup_task preserves the original runtime task. Maintain task_state before each call_me():
update goal and constraints when the user steers the task, and completed and pending as work progresses.
Record verified outcomes in completed; planned work belongs in pending. build_context() carries all fields on every call.
History summaries record intent; use cell output and errors to determine what actually happened.
""".strip()

RECENT = 10  # Finished task cells in the default context; all others remain in history.
var_docs = {  # Variable name -> description, maintained by the agent in this session.
    "startup_task": "Original runtime task; preserve when the current goal changes.",
    "task_state": "Current goal, constraints, verified completed work and pending work; update before call_me().",
}
_EXCLUDED_NAMES = {"task", "call_me", "call_llm_api", "quit", "history", "input", "source_code"}


def variables():
    """Select session variable names and types; this policy is editable in the REPL."""
    hidden = globals()["get_ipython"]().user_ns_hidden
    found = {}
    for name, value in globals().items():
        if name.startswith("_") or name in hidden or name in _EXCLUDED_NAMES:
            continue
        if isinstance(value, _types.ModuleType):
            continue
        kind = type(value).__name__
        try:
            kind += f"[{len(value)}]"
        except Exception:
            pass
        found[name] = kind
    return found


def build_context():
    """Select context from host history without modifying its original records.

    Core refreshes history before each cell. Variable selection, working instructions,
    var_docs, RECENT and this function are editable state in the persistent session.
    """
    history = globals()["history"]
    described = [
        f"{name}: {kind}" + (f" ({var_docs[name]})" if var_docs.get(name) else "") for name, kind in variables().items()
    ]
    lines = [
        f"Startup task: {startup_task}",
        f"Current goal: {task_state['goal']}",
        f"Constraints: {task_state['constraints']!r}",
        f"Completed: {task_state['completed']!r}",
        f"Pending: {task_state['pending']!r}",
        f"Working directory: {_Path.cwd()}",
        f"Current task cell: {len(history)}",
        "Variables: " + (", ".join(described[:200]) or "none"),
    ]
    # Older records stay accessible by index; summarize their intent and outcome here.
    start = max(0, len(history) - RECENT)
    for number, cell in enumerate(history[:start]):
        result = f"failed: {cell['error'].strip().splitlines()[-1]}" if cell["error"] else "ok"
        lines.append(f"cell {number}: {cell['summary']} [{result}]")
    blocks = []
    for number, cell in enumerate(history[start:], start=start):
        parts = [f"### cell {number} · {cell['summary']}", "code:", cell["code"].strip()]
        if cell["output"].strip():
            output = cell["output"].strip()
            if len(output) > 1000:
                output = output[:500] + f"\n[Full output: history[{number}]['output']]\n" + output[-500:]
            parts += ["output:", output]
        if cell["error"]:
            parts += ["error:", cell["error"].strip()]
        blocks.append("\n".join(parts))
    context = "\n".join([*lines, "Recent task cells:", "\n\n".join(blocks) or "none yet"])
    return [{"role": "developer", "content": instructions}, {"role": "user", "content": context}]


response = globals()["call_me"](input=[*build_context(), {"role": "user", "content": _core_agent.Agent.START}])
print(response.output_text)
