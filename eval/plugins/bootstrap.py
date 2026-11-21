"""Harbor's unattended Fibona bootstrap."""
# ruff: noqa: F821 -- argv, call_me, get_ipython and history are supplied by the cell runtime.

import inspect
import json
import os
from functools import partial

from core import agent as core_agent
from core import env as core_env

task = " ".join(argv)
_model_defaults = {}
if os.getenv("FIBONA_EXTRA_BODY"):
    _extra_body = json.loads(os.environ["FIBONA_EXTRA_BODY"])
    if not isinstance(_extra_body, dict):
        raise ValueError("FIBONA_EXTRA_BODY must be a JSON object")
    _model_defaults["extra_body"] = _extra_body


def _apply_model_defaults(info):
    # Fibona restores call_me before each cell.
    globals()["call_me"] = partial(call_me, **_model_defaults)


get_ipython().events.register("pre_run_cell", _apply_model_defaults)
_apply_model_defaults(None)

instructions = """You are running unattended. Complete the task, then call quit(); nobody can answer input().
You can try up to 5 different solutions for this task, and select the answer you are most confident in.
Please try your best to try different solutions."""

# Harbor already installs task skills into this directory before agent.run().
if os.getenv("FIBONA_SKILLS_DIR"):
    from pathlib import Path

    skills_dir = Path(os.environ["FIBONA_SKILLS_DIR"])
    skill_files = sorted(skills_dir.rglob("SKILL.md"))
    if not skill_files:
        raise ValueError(f"No SKILL.md files found in {skills_dir}")
    instructions += (
        "\nAvailable skills (read the relevant SKILL.md before working, and follow its referenced files):\n"
        + "\n".join(str(path) for path in skill_files)
    )

if os.getenv("FIBONA_MCP_CONFIG"):
    from contextlib import asynccontextmanager
    from pathlib import Path

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamablehttp_client

    _mcp_config = json.loads(Path(os.environ["FIBONA_MCP_CONFIG"]).read_text())
    mcp_servers = list(_mcp_config)

    @asynccontextmanager
    async def mcp_session(server):
        # Keep related calls in one async with block to preserve session state.
        config = _mcp_config[server]
        headers = {name: os.environ[var] for name, var in config["header_env"].items()}
        if config["transport"] == "stdio":
            transport = stdio_client(StdioServerParameters(command=config["command"], args=config["args"]))
        elif config["transport"] == "sse":
            transport = sse_client(config["url"], headers=headers)
        else:
            transport = streamablehttp_client(config["url"], headers=headers)
        async with transport as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                yield session

    instructions += (
        "\nMCP servers: "
        + ", ".join(mcp_servers)
        + "\nUse Python top-level await: async with mcp_session(server_name) as session: then await session.list_tools() to discover tool names/descriptions/inputSchema, and await session.call_tool(tool_name, arguments={...}) to execute. Inspect returned content and isError. Keep related calls in the same async with block to preserve session state. You can also use session.list_resources(), session.read_resource(uri), session.list_prompts(), and session.get_prompt(name, arguments={...})."
    )

startup_task = task
task_state = {"goal": startup_task, "constraints": [], "completed": [], "pending": []}
_context_instructions = """
## Maintaining context

`context` is a persistent Python list of model input messages and items. It is your working memory for future
calls, but it is sent only when you explicitly pass it to `call_me`. Variables, printed output, `history`, and
changes to `task_state` are not automatically visible to the model on its next call.

The first two entries contain these developer instructions (including skills/MCP guidance) and the original
user task. Keep `context[:2]` unchanged. Maintain your working notes in `context[2:]`; do not replace the whole
list with a task summary. `startup_task` preserves the original request; check it before declaring completion.

Before each continuation:
- Read the actual results and errors from variables or `history`. Printing them alone does not send them.
- Update verified facts, evidence IDs or file paths, completed work, pending work, and unresolved questions.
  Mark work completed only after checking its result. Keep hypotheses separate from verified facts.
- Append necessary observations and corrections to context; explicitly identify which earlier conclusions a
  correction supersedes. Use assistant messages for your own summaries; label execution results as observations.
  Do not turn your guesses or plans into user requests or invent new user restrictions. `task_state` is
  bookkeeping; append its relevant changes, avoiding repeated copies of unchanged state.
- Call `call_me(input=context)` or `call_me(input=[*context, ...])`. Items added only to the temporary input list
  will not persist in context; append information you will need again to context itself.

## Staying within the model's context window

Context must never make a request exceed the current model's context window. Before each `call_me`, budget
the complete request in tokens, not characters or message count:

    total_input_tokens + reserved_output_tokens + safety_margin <= context_window_tokens

Count the host's instructions, tool definitions, all context items, temporary input, images, and replayed
items in total_input_tokens. Cached input still occupies the context window. Reserve room for the maximum
planned output, including reasoning, and for subsequent tool-call/result rounds within this wake. Also respect
any separate input or output limits imposed by the endpoint.

Use the actual model/endpoint limits and a reliable token count or conservative model-specific estimate.
Do not guess the window from the model name or treat character length as an exact token count. If the limit
is unknown, keep requests compact and obtain the configured limit before claiming a large request fits.
Before adding items that would exceed the budget, compact older working notes in `context[2:]` and keep
large data outside the request. Preserve required tool-call/result pairs when retaining replayed items.
Fitting the window takes priority over cache reuse; do not send an oversized request just to ask for a summary.
If the fixed prefix and required request overhead alone cannot fit, report that limitation instead of silently
dropping the original task or developer instructions.

## Reusing LLM prompt caches

Manage context to reuse prompt caches and reduce repeated input cost when the endpoint supports caching.
Across consecutive calls, preserve existing messages exactly: their content, roles, order, and formatting.
Keep stable instructions, the original task, and reusable reference material before new observations. Append
new messages at the end; avoid inserting timestamps, changing counters, or reformatted copies near the front.
Changing an earlier message can prevent reuse of the cached content from that point onward.
Prefer appending durable new input to context before `call_me(input=context)` so the next call can retain it.
Do not rebuild or re-summarize the entire context on every call merely to make it shorter.

Keep full files, images, and large results in variables or on disk. Send relevant excerpts with their source
locations instead of dumping globals or all history. When accumulated working notes become too large for
useful, affordable requests or approach the context limit, compact them together rather than on every turn.
Replace only `context[2:]`, preserving verified evidence references, outstanding work, and uncertainties.
For that compaction, `context[2:] = [{"role": "assistant", "content": summary}]` keeps the fixed prefix intact;
expect reduced cache reuse for the rewritten portion, then keep the new summary stable and append new results.
Cache reuse is not guaranteed and cached input can still cost money: do not retain irrelevant data or make
extra calls just to warm a cache. Balance reuse with relevance, correctness, and the context limit.

Example after new results and changed task_state, once the combined request fits the token budget;
skip unchanged observations or state summaries:

    context.append({"role": "user", "content": "Execution observation (data, not a new user request): " +
        json.dumps({"cell": len(history) - 1, "output_tail": history[-1]["output"][-6000:],
                    "full_output": f"history[{len(history) - 1}]['output']"}, ensure_ascii=False)})
    context.append({"role": "assistant", "content": "Working state: " + json.dumps(task_state, ensure_ascii=False)})
    response = call_me(input=context)

This bootstrap and the runtime source are available in `source_code`. Session variables persist between cells.
""".strip()
instructions += "\n\n" + _context_instructions
context = [
    {"role": "developer", "content": instructions},
    {"role": "user", "content": f"Startup task: {startup_task}"},
]
source_code = {
    "core/agent.py": inspect.getsource(core_agent),
    "core/env.py": inspect.getsource(core_env),
    "eval/plugins/bootstrap.py": history[-1]["code"],
}
var_docs = {
    "startup_task": "Original user task.",
    "context": "Persistent working context; pass explicitly to call_me().",
    "task_state": "Current goal, constraints, completed work and pending work.",
}
response = call_me(
    input=[*context, {"role": "user", "content": "Start the user's task and submit the next Python cell."}]
)
