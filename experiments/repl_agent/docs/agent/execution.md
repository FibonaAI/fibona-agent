# Model access and execution

Part of the [agent design](README.md). Raw model access, recorded requests and recorded cell execution have separate responsibilities. Normal work and recovery choose different input while sharing execution semantics.

## Logical capabilities

| Capability | Input | Outcome |
| --- | --- | --- |
| Raw model request | Explicit ordered input and optional settings | Complete/incomplete/failed response, or a request error; no automatic recording or dispatch |
| Recorded request | Explicit input | Request and outcome associated with the starting session |
| Recorded cell execution (`run_cell`) | Source, optional execution budget and optional model-call association | Captured observations and execution outcome; interruption and recording failures retain their control meaning |
| Presentation (`display`) | Values for the user | Visible output separate from model observations |

Model settings and tool definitions remain editable. Read current settings for each request; later edits affect subsequent requests. The recorded diagnostics must describe the request actually sent rather than settings changed during its wait.

## Accepting model output

The model acts through `run_cell`, which executes source in the persistent language environment. A call supplies code and can set a time budget for that execution; otherwise the editable agent default applies.

Distinguish complete, incomplete and failed responses. Only a complete accepted response may dispatch calls. Validate enough to identify each call unambiguously, pair its result and execute valid source. Invalid tool names or arguments produce an associated dispatch error without executing a cell. Missing or ambiguous call identities and malformed envelopes cannot be treated as successful empty responses. Provider-specific metadata that is accepted must remain safe through recording and projection; do not let optional metadata break otherwise usable history.

Preserve complete provider output, including opaque items needed in later requests. Do not infer calls from code-like text or execute partial streaming output. Completed output with no calls ends the step; text plus calls does not. The provider adapter maps logical messages, accepted calls and results into its wire format without changing pairing or order. Missing usage is unknown, not zero.

## Request and batch ownership

Before sending, the agent records the request with a snapshot of the configuration selected for that attempt. If this record cannot be stored, the request is not sent. The response or request error is recorded afterward and associated with that request, preserving provider output, available usage and useful failure diagnostics. Failure to persist an outcome is a recording failure, not proof the request never happened.

One model step's selected input stays stable across response retries. Its requests, accepted response, calls and results belong to the originating session even if cell code changes a global session binding. A direct unlinked cell belongs to the session selected when it starts. Subsequent steps may use a deliberately selected matching session/Context pair.

Later edits to settings do not change the recorded configuration. In a persisted session, if the process stops before an outcome is recorded, the selected configuration remains inspectable, while the provider's outcome remains unknown.

## Recorded cell execution

Recorded execution wraps the REPL's retained raw executor in editable agent code. Model dispatch and direct nested calls use the same recorded execution semantics.

```text
validate source/budget and establish supported execution ownership
retain starting session and same-session parent
record start with source, call association and selected budget
capture model-facing output
execute in the shared environment under the supported deadline
on ordinary execution failure: retain output and diagnostics as an error result
on interruption: retain interrupted evidence where possible and propagate
always restore capture, deadline resources and execution ownership
record the end when storage remains usable
```

If the start cannot be stored, do not execute. If storing the end fails, do not claim successful recording or rerun the source. A rebind of global session cannot move an active cell's end to another log. Only cells linked to accepted model calls generate tool replies; unlinked nested cells remain inspectable facts.

Partial effects survive exceptions, deadlines and code rollback. A child captures its own output; a parent includes it only if it explicitly emits the child's result. Rejected competing execution must not disturb another cell's capture.

## Cell deadlines

Bound recorded cells with an editable finite positive default duration. Allow a caller to choose another finite positive duration for the expected work; validate it before execution.

A deadline belongs to the execution invocation, not merely its session-local cell ID. Nested work does not extend the enclosing budget. If a child's own deadline expires, the child reports an execution error and the parent can inspect it. If the enclosing deadline expires while awaiting a child, that expiry must reach the enclosing boundary rather than being swallowed as the child's ordinary failure. Distinguish this from an unrelated timeout exception raised by user code.

Expiry of the cell's own budget is an ordinary cell error with diagnostics and any available output; the next model step can inspect partial state. External cancellation is a request to stop, so it propagates without starting recovery or fallback. Always restore deadline resources and capture. Do not leave a timer that can interrupt a later cell or silently replace an unrelated active timer.

Interruption coverage depends on the platform. Python asynchronous cancellation is delivered at supported waiting points; some synchronous work requires additional platform facilities. In JavaScript, a timer cannot run while the event loop is blocked, so racing a promise against a timer does not interrupt arbitrary synchronous code.

A deadline is not a subprocess supervisor, rollback mechanism or sandbox. It may leave child processes, I/O and other external effects running; inspect and clean up resources before retrying.

## Failure boundaries

| Failure | Required behavior |
| --- | --- |
| Invalid tool arguments or rejected competition | No source effects; an accepted identifiable call receives a dispatch error |
| Ordinary cell exception or its own deadline expiry | Preserve observations and error result; no automatic replay |
| Model request or normal context/loop failure | Propagate to the agent's recovery boundary |
| Unusable recording infrastructure | Propagate and clean up; do not attempt model-driven repair on unreliable history |
| External cancellation | Propagate through request, cell and recovery wrappers; no new work or fallback |

Cleanup must not turn interruption into success or hide that recording failed. Preserve useful secondary diagnostics when cleanup also fails. A write of uncertain completion requires validation before further use of that storage.

## Output audiences

| Channel | History/context | Audience |
| --- | --- | --- |
| Explicit cell output and captured errors | Full evidence retained; projected view may be shortened | Model |
| `display` | Rendered text retained with its originating cell; not projected into model input | User |
| Model reply text | Preserved in response history and context; accepted complete replies are displayed once before tool execution | User |

Ordinary reply text (including refusals) is user-facing, even when the same response contains tools. Reasoning and incomplete/failed response text remain diagnostic evidence, not user presentation. Presentation bypasses cell capture, including nested model steps. Trajectories distinguish model replies, in-cell user display and model-facing execution feedback; they do not promote captured output into assistant prose.

Retain user presentation independently of capture. If both audiences need a value, explicitly write to both. Each display records the rendered text and originating cell in that cell’s session, or the current session with no cell association when called outside a cell. This evidence is independent of model-facing output; recording it must not add a tool reply or model input. Historical sessions without these records cannot reconstruct displayed values from source alone. The prompt must explain how to present questions, progress and final answers.

Process-stream capture cannot isolate unrelated background writers; those writes may appear among a cell's observations. Fact relationships and projection belong to [Session](session.md).
