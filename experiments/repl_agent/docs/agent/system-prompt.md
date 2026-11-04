# System prompt

Part of the [agent design](README.md). The prompt gives purpose, working principles and discovery paths into the live environment. It is editable agent source, shared by normal work and recovery.

## Organization

The prompt is organized into five top-level sections, allowing the agent to inspect and revise one aspect of its guidance without losing the others:

| Section | Guidance |
| --- | --- |
| Identity | The agent's identity and capacity to understand, modify and evolve itself in real time |
| Work | Understand the user's task and goals and complete them autonomously |
| Environment | Shared persistent REPL, core components (`instructions`, `session`, `context`, `run_cell`) and persistent task state and tool discovery (`task_state`, `tool_catalog`) |
| Self-iteration | Compare expectations with actual outcomes, reflect proactively, expand tools and capabilities, and validate replacements with rollback safety |
| Recovery mode | Shared REPL and basic capabilities with an independent context aimed at restoring normal-mode operation |

Sections remain concise and express durable concepts rather than a growing checklist of past failures. Concrete signatures and execution details stay in tool interfaces and callable documentation, with discovery paths from the prompt.

Each request uses the current guidance. Edits affect future requests, not those already sent. Recovery adds a repair instruction to its own input rather than replacing the agent with another persona.

## Environment and component subsections

The `Environment` section gives the agent a direct map of its live runtime: the agent and `run_cell` share the same persistent REPL, where variables and functions survive across cells and context resets, and where the agent can inspect and redefine its own code. It groups the key components defined in the REPL:

- `instructions`: the structured prompt sections passed to the model, editable for iteration.
- `session`: append-only, persisted historical facts covering messages, executed code, output and errors across contexts.
- `context`: organization and budget management of the model input window, with explicit handoff compaction and a reminder near the window limit; compaction only changes model input, not the REPL environment.
- `run_cell`: the sole model-facing tool for executing code in the REPL with recording and timeout handling. Guidance highlights managing variables to avoid unbounded memory growth, combining multiple steps in one cell and sharing data across cells for efficiency, distinguishing model-captured `print` output from user-facing output, and separating agent dependencies from the task's working environment.
- Other REPL variables and functions:
  - `task_state`: an initially empty container for task notes and state, outside automatic context projection.
  - `tool_catalog`: an index of function names and purpose descriptions, outside automatic context projection. Built-in tools can be looked up and used first; they are also defined in the REPL and can be modified and improved. The agent can update the catalog with reusable functions and tools to expand and improve its capabilities.

Concrete local tools are discovered through the catalog rather than listed individually in the prompt. The catalog and task notes remain editable live state and do not automatically enter context.

Implementations share the same section structure and component guidance; runtime names and container descriptions follow the implementation language.

## Self-iteration and recovery guidance

`Self-iteration` directs the agent to compare expectations with actual outcomes, analyze historical behavior, expand capabilities and tools, and iterate on itself safely. Because redefinitions in the REPL take effect immediately for dynamic lookups while active calls and saved references keep their existing bindings, replacements should be validated and tested first, using `try` inside a cell to catch exceptions and roll back when needed; see [self-modification](README.md#self-modification-and-dependency-stability).

`Recovery mode` explains what happens when a failure prevents normal mode from continuing: recovery runs in the same REPL and shares foundational capabilities such as `instructions`, `run_cell` and `session`, but uses an independent context whose goal is to restore normal-mode operation.
