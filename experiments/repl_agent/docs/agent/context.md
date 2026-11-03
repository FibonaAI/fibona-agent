# Context

Part of the [agent design](README.md). Context selects working input from a Session. It does not own history, live task state or the model client. Normal work uses it before each model step; recovery bypasses it so a broken policy can be repaired.

## Construction

Each Context is bound to a specific session. Changing a global session binding does not silently redirect an existing Context. Each build produces a fresh input view from the current retained-history boundary and normal-work facts, with continuation information as needed.

After a reset, include the agent-written handoff or a notice pointing to earlier history and live state. After process restart, explain that only records were restored. Do not claim old variables or task state survived a new process. A deliberate reset during recovery can change the next normal view.

Large observations may be shortened in the view, with a usable pointer to the full evidence. Never shorten stored facts. Keeping both head and tail helps preserve initial observations and final diagnostics.

Task state is not automatically copied into input. The agent selects and emits relevant parts through cell output or an explicit handoff. This keeps maintenance of live working notes separate from the finite model window.

## Reset

Reset records a new boundary with an optional agent-written handoff and a diagnostic cause where useful. It does not call a model, generate a summary, delete history, clear live objects or change execution mode. Failed persistence must not advance the effective boundary.

When a model response contains several calls, Context retains that response and all corresponding results until the unfinished calls are resolved. Resetting inside either of two calls must preserve the other call’s result as well, even if it has already finished. This keeps the next input understandable to the model: every result answers a call, and every accepted call has a result. Recovery calls remain separate from normal input.

A simple reference algorithm chooses the earliest response containing an unanswered normal call; if none exists, start after the discarded conversation. Building during an active batch is read-only and may expose pending work; wait for completion or repair before sending it as normal conversation input.

## Budget management

The input budget reserves room for the configured maximum output and must be positive. Context estimates the actual request's input cost, including current instructions and tools. An early warning leaves room for the agent to maintain context; an estimate above the allowed budget causes the work loop to enter recovery before sending the oversized normal input.

Input size can be estimated from its text length and checked against recent measured token usage. Adding the growth since the last measured request helps account for new observations. A warning before the budget is exhausted leaves room for the agent to write a handoff and reset context. Estimation can still differ from the provider’s token accounting.

If measured usage is used, recovery usage and pre-reset input must not inflate a fresh normal view. Response output and later tool results add new input beyond the previous measured request. Missing usage remains unknown. Include added notices and advice when deciding whether the final input fits; the check concerns the request to be sent, not just an earlier intermediate list.

Advice is request guidance, not a new historical event or an automatic reset. Context maintenance is the agent's decision: preserve goals, progress and retrieval pointers, inspect relevant state, then reset or reorganize deliberately. Returning from recovery alone does not imply that maintenance happened.

## Relationship to model requests

`build` selects input, `reset` changes its retained-history boundary, and `estimate` assesses its size. These operations prepare the next decision without requesting a model response themselves. The work loop uses the resulting view; Session remains the source of historical evidence.
