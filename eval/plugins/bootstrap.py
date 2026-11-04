"""Harbor's unattended Fibona bootstrap."""
# ruff: noqa: F821 -- argv, call_me and get_ipython are supplied by the cell runtime.

import json
import os
from functools import partial

from bootstraps.terminal.context import initialize_context

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

build_context = initialize_context(globals())
response = call_me(
    input=[*build_context(), {"role": "user", "content": "Start the user's task and submit the next Python cell."}]
)
