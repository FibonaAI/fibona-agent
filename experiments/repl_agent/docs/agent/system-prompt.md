# System prompt

Part of the [agent design](README.md). The prompt gives purpose, working principles and discovery paths into the live environment. It is editable agent source, shared by normal work and recovery.

## Organization

The prompt separates four concerns, allowing the agent to inspect and revise one aspect of its guidance without losing the others:

| Section | Guidance |
| --- | --- |
| Identity | The agent's purpose and intent to improve its ways of working |
| Working | Advance the task through evidence, action and verification |
| Environment | Discover and maintain capabilities, state and resources |
| Reflection | Investigate discrepancies and evaluate changes to approach or environment |

Each request uses the current guidance. Edits affect future requests, not those already sent. Recovery adds a repair instruction to its own input rather than replacing the agent with another persona.

## Working and reflection

Teach the agent to establish goals, constraints and completion evidence; work independently while useful progress is possible; and distinguish assumptions, attempts and verified outcomes. Unexpected results should stop dependent work and trigger inspection rather than blind repetition.

Investigations should resolve a specific unknown. Start with a small check and a suitable budget, then expand when evidence warrants it. Verify actual deliverables against the user's goal, including checks capable of disproving assumptions. Preserve useful progress when changing direction and report unresolved uncertainty.

Reflection can change understanding, plans, tools, state organization, context, prompt or the work loop. Adding a permanent rule is not the default response to one incident. Preserve conditions and evidence for lessons, apply them, and evaluate whether they help.

## Environment guidance

Environment guidance gives the model starting points for discovering capabilities and understanding the consequences of its actions:

| Concern | Explain |
| --- | --- |
| Execution | Target language, persistent shared namespace/process, asynchronous use and the sole cell tool |
| Discovery | Complete first-cell source, execution history, session facts, current bindings, instructions, settings and Context |
| Task state | Editable working notes such as `task_state`; maintain them actively and explicitly inspect/emit relevant parts |
| Lifetimes | Reset and recovery preserve live state; restart restores records only; memory persistence requires explicit action |
| Context | Finite working input, retrieval pointers and deliberate reset without history deletion or automatic summary |
| Output | Captured observations go to the model; `display` reaches the user; accepted ordinary model reply text automatically reaches the user before tools |
| Resources and time | Cells are not disposable scripts; bound work, await nested cells sequentially, manage I/O and clean up resources |
| Self-modification | Inspect callers and dependencies, preserve a rollback path, verify real usage and revert failed changes |

Task state and model context need active maintenance rather than endless accumulation. Revise stale notes, consolidate finished work, move large evidence to retrievable locations and preserve a useful handoff before transitions. Task state is not injected automatically; explicitly choose what the model needs next. Recovery is not a new task.

Source, history and current bindings can disagree. A successful cell may still overwrite a dependency used later. Prefer local scopes for ordinary work and distinct names for persistent task data. Protect basic recording, model access and execution by default, while allowing deliberate verified changes.

The shared process is also the agent's process. Blocking or terminating it affects the work loop. A cell deadline does not supervise child resources or guarantee interruption of native code. Failures, retries and rollback do not undo external effects; inspect before repeating actions. Do not promise isolation that the REPL lacks.

## Guidance in the live environment

Prompt sections carry durable working principles. From the discovery pointers, the agent can inspect the code that provides its capabilities and learn how to use them. Changing task progress stays in working state and history, so it does not become permanent instruction.

Instructions guide use of execution boundaries, recording and context policy; code must implement those mechanisms. When changing guidance, inspect the composed prompt for contradictions, preserve the old version and verify the next real request path. Remove obsolete guidance rather than accumulating patches.
