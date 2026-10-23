# Working on new_core

These instructions are for coding agents editing this directory. They are not the cell-0 system prompt and are not automatically sent to the running model. See [README.md](README.md) for usage.

## Purpose and boundaries

This experiment centers on agent self-observation, self-modification and iteration. Provide a few composable capabilities so the model can understand and maintain itself through code. Do not move work policy into the host or add frameworks and prompt patches for isolated model mistakes.

- **Launcher:** select a runtime, read a bootstrap, execute source and close the environment. Do not inject agent arguments or source variables, call a prescribed agent entry point or interpret its return value.
- **Runtime:** maintain persistent execution, bindings, source history and execution boundaries. It does not own sessions, context, model access, presentation or recovery policy.
- **Bootstrap:** own the complete agent, prompt, interaction, recording and loops. The current complete implementation must enter cell 0; the bootstrap records its source from `In`.
- Runtime and bootstrap may be selected independently or fixed together for a device. Platform instructions belong with the bootstrap; universal portability is not required.
- Platform directories do not imply manifests, registries, automatic loaders or a shared cross-language framework. Supporting files do not automatically enter cell 0.

Make only task-relevant changes. Do not replace the original repository entry point or clean up other experiments. Inspect and preserve unrelated concurrent edits.

## Code map

| Concern | Location |
| --- | --- |
| Startup selection | `main.py`, `main.js` |
| Persistent environment and nested execution | `runtimes/python/runtime.py`, `runtimes/node/runtime.js` |
| Agent, prompt, model configuration and interaction | Each `bootstraps/<platform>/main.*` |
| Session, context, recovery and self-modification tests | `tests/test_core.py`, `tests/node.test.js` |

Use signatures and comments as the interface reference. Explain intent and actual boundaries rather than duplicating an API manual across documentation, prompts and code.

## Preserve core semantics

### Model requests and execution

- `request_model` takes explicit input. It does not select history, record responses or execute returned code. Keep model-dependent defaults together at the top of the bootstrap; explicit request options may override them.
- The bootstrap retains the raw executor and defines its recorded `run_cell`. Model tools and nested execution use the same function; do not add another wrapper entry point.
- Record and return ordinary execution errors, retaining partial effects. Do not disguise storage failures as successfully recorded execution or cancellation as normal success.
- Allow directly awaited nested cells and reject concurrent cells. Finish concurrent I/O before returning. Do not introduce a background scheduler or execution queue.
- Python raw execution returns no expression value; Node can return one. The bootstrap does not use trailing expressions as output. Write values explicitly to the intended channel.
- Use replaceable `var` bindings or assignments for Node self-modification. Native REPL exception routing uses an internal error domain; verify this boundary when changing the runtime or Node version.

### Session and context

- Session is an append-only log of independent facts, not a mutable task-state object. Keep it linear, without branches or a general event-dispatch framework.
- `cell_start.id` is the cell ID. `cell_end.cell_id` identifies its start; `parent_cell_id` links nesting. Only a cell corresponding to a model tool call carries that `call_id`.
- Store model responses and usage in `model_response`, and execution output in `cell_end`. Derive tool results through projection rather than storing duplicates. Reading views are derived data.
- Bind recording to the session used when execution starts. Rebinding a global during a cell must not send its end record to another log.
- Context binds explicitly to a session and builds working input. Keep full history in session; truncate output only in the model view.
- `context.reset` appends a boundary and optional agent-written handoff. It never requests a model, generates a summary, clears objects or changes mode. Preserve complete call/result pairing for an active tool batch.
- Restart never replays effects. Bootstrap startup closes unfinished records and repairs unanswered calls without duplicating repairs on repeated startup. Do not implicitly migrate or overwrite old formats or corrupt data.

### Work, recovery and self-modification

- Normal and recovery modes share the system prompt, REPL, session, model access and cell executor. Record mode transitions in session.
- Recovery bypasses normal context policy and uses fresh input to inspect history and live state. Reuse response handling and fact projection rather than building a second agent.
- Allow at most one automatic recovery attempt per user turn. After a completed handoff, try the current work loop. A handoff is not proof of repair; subsequent execution verifies it.
- Assume execution, model access and storage remain available. Do not promise recovery from arbitrary core damage, hangs or process exit.
- Self-modification should retain working definitions and arrange switching, validation and rollback within the same cell. Rollback must not depend on another model call or the newly broken helper. Verify the actual call path, not merely a successful assignment.
- Organize the prompt into `identity`, `environment`, `working` and `adaptation`. Explain goals, discovery paths and principles; let the agent discover implementation details from source and history. The prompt itself is editable.

### Output audiences

- Cell `output` is for the model. Python `print` and Node `console.log` feed captured output into session records and projected tool results for subsequent context. This is not a user presentation API.
- `display` is for the user. It bypasses cell output capture and does not append its rendered values to session or context. Use it for progress, questions and final answers.
- Cell source, including display calls, remains recorded. Do not confuse recorded source with a record of the values actually displayed. If both audiences need a value, write it to both channels explicitly; do not automatically duplicate it.
- Model reply text remains recorded, without automatic user presentation. Necessary bootstrap stop notices may still be displayed.
- Keep these responsibilities in the bootstrap. Do not introduce an output router, event layer or second transcript to enforce this distinction.

### Platform boundaries

- Python uses the SDK; Node uses native `fetch` without extra automatic HTTP retry. Both retry incomplete responses once without executing partial tool calls.
- Output capture affects process streams and does not isolate unrelated background writers. Neither runtime is a sandbox.

## Verify changes

Run the relevant README checks. Check both platforms for shared semantic changes; do not mechanically port platform-specific details. For documentation-only edits, verify source, commands and links without paid model calls.

For changes to loops, context or self-modification, verify:

- Session exposes complete bootstrap source and later history; nested IDs, output and tool results remain correctly associated.
- Context reset preserves active batches and supports both handoffs and reconstruction from the original session and REPL.
- Ordinary errors, incomplete responses and recovery failures retain clear boundaries without repeating completed effects.
- Model-facing output reaches tool results. Displayed dynamic values reach the user but do not leak into records or context; the display call's source remains available.
- Self-modification changes the subsequent real execution path, while a failed candidate restores working definitions in the same cell.

Use separate temporary sessions for live model checks and retain inspectable execution evidence. Do not modify repository source or user sessions during such checks. Report offline tests and live requests separately; mocked responses are not live model validation. Keep transient test counts, temporary logs and isolated model errors out of long-lived documentation.
