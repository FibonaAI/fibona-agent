# Agent design

The agent is ordinary editable code in the REPL's first cell. It owns what the model sees, how accepted calls execute, how evidence is retained and how work resumes after failure. The [project design](../design.md) explains its relationship with the REPL. This page describes the agent’s internal state and how its components work together.

## Core decisions

Normal work selects input through Context. Recovery builds independent input from the same Session. Both use the same model access, response handling and recorded executor. Recovery is a mode of this agent, not another process or a second agent implementation.

Keep four roles distinct:

| State | Purpose | Lifetime and visibility |
| --- | --- | --- |
| Execution environment | Variables, functions, clients and other resources retained in the REPL | Current process; inspectable from cells |
| Task state | Editable working notes and retrieval pointers, conventionally `task_state` | Part of live state; not automatically sent to the model or restored on restart |
| Session | Append-only evidence of what was submitted, observed and executed | In memory or persistent storage; complete even after context reset |
| Context | Selection and presentation of evidence for the next request | Rebuildable view; does not own history or live objects |

Task state begins as an empty ordinary container. The agent decides its organization, updates stale notes, consolidates progress and puts large evidence elsewhere with retrieval pointers. Notes are maintained deliberately rather than generated as automatic summaries or inserted into every prompt. To use it, inspect and explicitly emit relevant selections through cell output. Context reset and recovery preserve it; a new process starts fresh unless the agent deliberately persists and restores selected information.

## Startup and interaction

1. Define the complete agent in the persistent namespace; retain raw execution and access to its source.
2. Establish editable prompt/settings, task state, session, context and user presentation. Validate required configuration and acquire resources as needed.
3. Repair unfinished historical executions without replaying them, then record this startup's complete agent source.
4. Read a non-empty user input, record it once and start normal work. Blank input is ignored; later turns share the live environment.
5. On EOF or an unhandled exit, release agent-owned resources. The launcher closes the REPL after the first cell returns.

A single-task entry may run one input and return. Loading history does not automatically restart the old task; explicit input starts work. Initialization errors propagate with cleanup because there is no valid work loop to repair yet.

## Normal work

```text
record the user input once
repeat:
    try:
        run the current work loop
        finish this turn
    on an ordinary loop failure:
        await the current recovery flow
        # If it returns, retry this same turn; if it fails, leave the turn.
    on cancellation or unusable recording infrastructure:
        propagate and clean up

work loop:
    build input with the current Context
    check the request budget
    run a shared model step
    if it handled tools: repeat with updated evidence
    otherwise: finish the turn
```

Resolve replaceable policies through current live bindings on subsequent invocations. Active calls are not rewritten by rebinding a name. A normal iteration uses a matching session and Context; do not send one log's projected calls while recording results in another.

Accepted model reply text stays in history and is shown to the user once before executing tools. Code uses `display` for user-facing content produced during cell execution. A completed response without tools ends the turn, even if its output is empty. A response with text and tools executes the tools and continues. An ordinary cell error is an observation for the next model step, not itself a reason to enter recovery.

## Shared model step

Normal work and recovery use the same sequence:

1. Make a recorded request with explicit input.
2. Accept only a complete response for execution. Preserve incomplete output as evidence, without executing it or replaying it as accepted calls.
3. Process tool calls sequentially. Invalid arguments produce a result explaining the dispatch error; valid calls use recorded execution. Ordinary execution errors remain results.
4. Continue if the response contained calls; otherwise complete the step.
5. If dispatch stops early, resolve unanswered calls in this step's batch without executing them. Do not close active enclosing cells or another step's pending siblings.

Use a bounded response retry policy for incomplete output and transient provider failures. Attempts concern the same selected input, and retries must never repeat already executed effects. Record retry decisions. Transport retries belong to the provider client and must be considered separately so total retry behavior is understandable. Permanent failures and exhausted attempts leave the step.

Retry policy balances a chance to recover from transient failure against the cost of delaying work. Waiting between failed attempts can avoid repeatedly hitting an overloaded service. Cancellation and unusable storage are never retryable model failures.

## Recovery mode

An ordinary failure escaping the normal work loop pauses the task and starts recovery. Recovery uses fresh input built independently of normal Context, while sharing the same REPL, task notes, history, prompt and execution capabilities.

Repair aims to make normal work usable again. When it returns, the agent resumes the same user turn. If repair fails ordinarily, it creates a fresh Context with a diagnostic handoff; cancellation and failed fallback propagate.

[Recovery mode](recovery.md) explains entry conditions, independent input, repair, fallback and return to the task.

## Self-modification and dependency stability

Source history, recorded facts and live bindings may disagree. Inspect them together with actual behavior before changing a capability.

For example, replacing the function that builds model input should affect the next work-loop iteration. Python functions normally resolve global names in their defining namespace when called, so rebinding a helper there can affect later calls. In a JavaScript REPL, lexical declarations, reassignable variables and module exports behave differently. In either language, a caller retaining the old function reference may continue to use it; the check must follow the actual calling path.

```text
identify the intended change and affected callers/dependencies
→ preserve working definitions and necessary state
→ install the candidate
→ check through its actual usage path
→ restore within the same cell if the check fails
```

Rollback must not require another model request or the broken capability. Saved references are not snapshots of mutable dependencies. Code rollback does not undo external effects. For behavior that can only be evaluated later, retain an independent rollback path and state what remains unverified.

Task code can overwrite a library name shared with agent helpers. Python function-local imports and JavaScript dependencies retained in local scopes reduce the effect of incidental name reuse on recording, error reporting and transport. These references do not freeze shared mutable objects. Policy bindings such as prompt, context, tools and loops remain deliberately editable.

The prompt should encourage bounded investigations that resolve a specific unknown, checks against the actual deliverable and constraints, preservation of useful progress and explicit uncertainty. Mechanisms belong in code; durable guidance belongs in prompt sections; changing task progress belongs in task state and evidence.

## Components

| Component | Responsibility | Detail |
| --- | --- | --- |
| Recovery | Independent repair input, fallback and continuation of the current task | [Recovery mode](recovery.md) |
| Prompt | Editable identity, working guidance, environment discovery and reflection | [System prompt](system-prompt.md) |
| Model access | Explicit input and current request settings; recording separate from raw access | [Execution](execution.md) |
| Recorded execution | Shared REPL execution, deadlines, evidence and separate output audiences | [Execution](execution.md) |
| Session | Append-only facts, projection and restart repair | [Session](session.md) |
| Context | Input selection, reset and budget management without automatic summarization | [Context](context.md) |

These components are responsibilities within the same live agent. Only cell execution is a model tool; other capabilities are available to cell code. [Behavioral properties](../validation.md) illustrate the resulting behavior.
