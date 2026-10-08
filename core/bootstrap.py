"""Executed once as the first IPython cell, not imported as a module.

The host supplies task, history, call_me and variables through the session namespace.
Later cells may redefine these working instructions and context functions.
"""

from pathlib import Path as _Path

# Read the existing host bindings; history and call_me are refreshed before each cell.
task = globals()["task"]
history = globals()["history"]
call_me = globals()["call_me"]
variables = globals()["variables"]

instructions = """
These are your initial context and memory conventions, defined by core/bootstrap.py as the first cell.
It defines instructions, var_docs and build_context(). You may change these values and functions
in this session; later calls can use your revised implementation. These conventions supplement the host's
fixed agent instructions. source_code["core/bootstrap.py"] contains the initial implementation.

- Use call_me(instructions=instructions, context=build_context(), input=observations) by default.
  You can choose different instructions and context for each call.
- **Variables** persist. The default build_context() includes their names, types, and descriptions from
  `var_docs`. Set or update `var_docs["name"] = "description"` when creating or changing a variable. Keep
  anything a later step needs in a well-named variable; prefix scratch values with `_` to hide them.
  Remove its description when deleting a variable.
- **`history`**: the default build_context() includes the last ten cells, and a one-line summary and result
  of each older one; read an older cell from `history[i]` in a cell. Long output is shown as its first and
  last 500 characters; read `history[i]["output"]` for the full text.
- **output** (`print`) is for the user. Print progress and findings for them; keep data for yourself in
  variables and point to them by name.

""".strip()

RECENT = 10  # Finished cells shown on each wake; older ones stay in `history`.
var_docs = {}  # Variable name -> description, maintained by cells.


def _clip_output(output, number, limit=1000):
    """Select an output excerpt without changing the original execution record."""
    if len(output) <= limit:
        return output
    note = f"[... {len(output) - limit} chars omitted; full output: history[{number}]['output']]"
    return f"{output[: limit // 2]}\n{note}\n{output[-limit // 2 :]}"


def build_context():
    """Context for each wake: task, cell number, variables, finished cells.

    Values live in the persistent Python session. build_context combines their
    names and types with these records, so each wake can see what has happened
    and continue the same task. Cells explicitly pass this context to call_me.

    Example (cell 14; `report` has no description in `var_docs`):
        Memory (written by the agent's runtime; not the user's words, not yours):
        - The user's task, verbatim: Summarize the Hacker News front page
        - Current cell: 14
        - Variables defined by earlier cells: pages: dict[30] (url -> html, fetched live), failed: list[2], fetch_all: function, report: str
        - Older cells (summary and result; read `history[i]` in a cell for the rest):
        cell 0: Start the task. [ok]
        cell 1: Fetch the front page [failed: HTTPError: HTTP Error 403: Forbidden]
        cell 2: Fetch with a browser User-Agent [ok]
        cell 3: Fetch every linked article [ok]
        - Recent cells (the last 10, with long outputs excerpted):
        ### cell 4 · Check which fetches failed
        code:
        ...

        ### cell 12 · Summarize each page in one line
        code:
        summaries = [call_llm_api("One line:\n" + html) for html in pages.values()]
        output:
        Summarized 30 pages.

        ### cell 13 · Write the report
        code:
        open("report.md", "w").write(render(summaries))
        error:
        Traceback (most recent call last):
        ...
        NameError: name 'render' is not defined
    """
    described = [
        f"{name}: {kind}" + (f" ({var_docs[name]})" if var_docs.get(name) else "") for name, kind in variables().items()
    ]
    lines = [
        "Memory (written by the agent's runtime; not the user's words, not yours):",
        f"- The user's task, verbatim: {task}",
        f"- Working directory: {_Path.cwd()}",
        f"- Current cell: {len(history)}",
        "- Variables defined by earlier cells: " + (", ".join(described[:200]) or "none"),
    ]
    # One line per cell before the recent window: its summary and whether it failed.
    if older := history[: max(0, len(history) - RECENT)]:
        lines.append("- Older cells (summary and result; read `history[i]` in a cell for the rest):")
        for number, cell in enumerate(older):
            result = f"failed: {cell['error'].strip().splitlines()[-1]}" if cell["error"] else "ok"
            lines.append(f"cell {number}: {cell['summary']} [{result}]")
    blocks = []
    for number, cell in enumerate(history[-RECENT:], start=max(0, len(history) - RECENT)):
        parts = [f"### cell {number} · {cell['summary']}", "code:", cell["code"].strip()]
        if cell["output"].strip():
            parts += ["output:", _clip_output(cell["output"], number).strip()]
        if cell["error"]:
            parts += ["error:", cell["error"].strip()]
        blocks.append("\n".join(parts))
    return "\n".join(
        [*lines, f"- Recent cells (the last {RECENT}, with long outputs excerpted):", "\n\n".join(blocks) or "none yet"]
    )


response = call_me(
    instructions=instructions,
    context=build_context(),
    input=f"Start the user's task and submit the first Python cell.\n\nUser task:\n{task}",
)
print(response.output_text)
