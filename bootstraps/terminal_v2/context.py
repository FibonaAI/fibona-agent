"""Session state for the cache-friendly terminal bootstrap."""

import inspect
from pathlib import Path

from core import agent as core_agent
from core import env as core_env

CONTEXT_INSTRUCTIONS = """
`startup_task` is the original user task. `task_state` is optional bookkeeping for the current goal, constraints,
completed work and pending work. Update it when the task changes, but copy only the parts needed for the next call
into `context` when you want yourself to see them.

The implementation and runtime source are available in `source_code`. Session variables persist between cells.
""".strip()


def initialize_context(namespace) -> None:
    """Install persistent session state without constructing a request payload."""
    task = namespace["task"]
    session_instructions = namespace.get("instructions", "")
    namespace.setdefault("startup_task", task)
    namespace["instructions"] = "\n\n".join(
        part for part in (namespace.get("instructions", ""), CONTEXT_INSTRUCTIONS) if part
    )
    context = namespace.setdefault("context", [])
    if session_instructions:
        context.append({"role": "developer", "content": session_instructions})
    context.append({"role": "developer", "content": CONTEXT_INSTRUCTIONS})
    context.append({"role": "user", "content": f"Startup task: {task}"})
    namespace.setdefault(
        "task_state",
        {"goal": task, "constraints": [], "completed": [], "pending": []},
    )
    namespace["source_code"] = {
        **namespace.get("source_code", {}),
        "core/agent.py": inspect.getsource(core_agent),
        "core/env.py": inspect.getsource(core_env),
        "bootstraps/terminal_v2/context.py": Path(__file__).read_text(encoding="utf-8"),
    }
    namespace.setdefault("var_docs", {}).update(
        {
            "startup_task": "Original user task.",
            "context": "Optional persistent Responses API input items; pass explicitly to call_me().",
            "task_state": "Optional current goal, constraints, completed work and pending work.",
        }
    )
