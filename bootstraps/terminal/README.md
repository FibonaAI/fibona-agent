# Terminal bootstrap

This bootstrap puts an agent's working instructions, memory organization, task helpers and terminal UI in a persistent Python REPL. The agent can inspect and change them as it works.

It is one agent implementation built on Fibona's minimal core.

## Design philosophy

The agent writes the program that drives its work. Cells perform operations, keep state and decide when to call the model for the next cell. The host executes and records cells; the generated program organizes the task.

This bootstrap extends that idea to working instructions, memory organization and the UI. They are part of the running program, so the agent can revise how it selects context, tracks a task and interacts with the user. It can replace the entire UI or change frameworks during a session.

The initial program brings up the environment and waits for user input before calling the model. Core executes cells, preserves the original history and handles recovery; the program owns its working strategy and interface.

Changes take effect when new definitions are executed in the session. Editing source files alone does not update running objects, and session changes are not automatically preserved across runs.

## Run

Requires Python 3.12+ and `uv`. Configure `.env` using the [project README](../../README.md), then run from the project root:

```bash
uv run --env-file .env fibona.py --boot-mode terminal
```

The welcome screen waits for a task before any model call. Press **Enter** to send and **Alt+Enter** for a newline. Later `input()` calls use the same input box. User-facing dialogue is printed by the chosen cells.

Click cell titles to expand or collapse highlighted source, and use the mouse wheel to scroll history. Drag to select text; releasing the mouse copies it. **Ctrl+C** copies an active selection; while a cell runs it stops the cell, and the agent waits for your next message. Otherwise, or pressed again while stopping, it exits.

Cells, outputs and errors are saved to `~/.fibona/<session_id>/session.ipynb`. Click the `notebook` file link in the status bar to open it with the system's default application.

The current directory is the workspace. Cells and model calls are recorded automatically in a separate `logs/<timestamp>-<run-id>.jsonl` file for each run.
