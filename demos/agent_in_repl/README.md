# Agent in REPL

This demo puts an agent's working instructions, memory organization and task helpers in a persistent Python REPL. The agent can inspect and change them as it works.

It is one agent implementation built on Fibona's minimal core.

## Design philosophy

The agent writes the program that drives its work. Cells perform operations, keep state and decide when to call the model for the next cell. The host executes and records cells; the generated program organizes the task.

This demo extends that idea to working instructions and context selection. [`bootstrap.py`](bootstrap.py) provides a usable starting implementation in the REPL. The agent can redefine it during a task, and later cells use the revised behavior.

[`runtime.py`](runtime.py) only overrides the core's first cell. Execution, original history and recovery stay in the host; working strategy and memory organization stay in the REPL. Changes affect the current session and are not automatically evaluated or preserved across runs.

## Run

Requires Python 3.12+ and `uv`. Configure `.env` using the [project README](../../README.md), then run from the project root:

```bash
uv run --env-file .env -m demos.agent_in_repl.runtime "Your task here"
```

The current directory is the workspace. Add `--debug` to record cells and model calls in `agent.log`.

## Use as a wrapper

Use the same `mind` and `env` as the core example, replacing `Agent` with `REPLAgent`:

```python
from demos.agent_in_repl.runtime import REPLAgent

REPLAgent(mind, cwd=".").run("Your task here", env=env)
```

## Tests

```bash
uv run python -m unittest demos.agent_in_repl.test_agent_in_repl -v
```
