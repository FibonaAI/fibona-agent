# Session

Part of the [agent design](README.md). Session preserves evidence independently of what Context selects for the next model request. It is an append-only history, not the mutable task-state container and not a checkpoint of process memory.

## Interface and responsibility

Session supports creation, loading, appending facts and reading ordered history. Message, cell and usage views are derived from that history rather than stored as independent copies. Loading alone does not execute code or repair pending calls; agent startup owns repair.

Each fact has a stable local identity and type, with enough ordering and timing information for inspection. Appended values must not change when a caller later mutates its input. Records and derived views are read-only, preserving the recorded facts for later inspection.

The history connects the following kinds of facts:

| Fact | Information and purpose |
| --- | --- |
| Session identity | Stable identity and storage-format identity/version |
| Agent source | Complete source evaluated at this startup |
| Message | Explicit user or agent-added input |
| Model request | Snapshot of the configuration selected for this attempt, attempt information and originating work/recovery scope; recorded before sending |
| Model response | Associated request, completion state, complete output, available usage and provider diagnostics |
| Request error | Associated request and failure/interruption diagnostics |
| Retry decision | Reason and associated attempt/response; does not prove the next request happened |
| Cell start | Source, parent if any, model-call association if any and selected execution budget |
| Cell end | Associated start, captured output, error and completed/error/interrupted outcome |
| Dispatch error | Accepted call that could not start; no fabricated cell execution |
| User presentation | Rendered display text and originating cell; retained separately from model observations |
| Mode transition | Normal/recovery entry or exit, recovery identity and useful diagnostics |
| Context reset | Retained-history boundary, optional handoff and cause |
| Recovery failure | Repair invocation, original failure and repair failure, usable by fallback |

Provider IDs, local record IDs and REPL source indices have different meanings. Missing usage is unknown. Preserve original provider data needed for inspection and later request construction.

## Ownership and consistency

Responses belong to their requests; ends belong to their starts; model-linked cells and dispatch errors answer accepted calls. A parent cell belongs to the same session. Keep an operation's association stable across global rebinding, recovery transitions and restart repair.

Mode filtering must preserve those relationships. For example, a normal call repaired after a crash during recovery still answers the normal call. The point at which a fact is appended does not change which operation it belongs to.

Generated history must not claim two terminal outcomes for the same operation, fabricate execution for rejected calls or attach results to the wrong request. Loading rejects incompatible or corrupt data rather than interpreting it as valid evidence. A missing terminal result is unfinished history, not proof of failure or permission to replay.

## Persistence

Session can keep history in memory or persist it. With persistent storage, a fact enters the in-memory history only after a successful append to storage. A failed or uncertain write stops further recording and propagates the failure. Later loading must establish a usable history before normal work resumes.

Single-writer versioned JSONL is a simple reference format. It permits inspection and append-only writes without a database. For this choice, validate the header, record identities and complete records; reject incompatible formats and interior corruption without overwriting them. An unparsable unfinished final fragment after a valid prefix may be discarded. A valid final record lacking a newline must be preserved. A parseable but invalid record is not a torn JSON fragment.

Other formats can use their own recovery mechanism while preserving append-only facts and the no-replay rule. Do not silently migrate old data or reuse a format version with incompatible meaning. Append-only history alone does not guarantee that a write survives power loss.

## Projection into model input

Projection turns selected history records into the messages and tool results used as model input. It reads the recorded facts without changing them:

- Explicit messages and complete accepted model output contribute input items, including opaque provider items needed for continuation.
- Model-linked cell ends synthesize tool results. Dispatch errors synthesize error results. Do not also persist these results as duplicate message facts.
- Unlinked direct/nested cells, source and request diagnostics remain inspectable history without extra tool replies.
- Incomplete/failed response output remains diagnostic history and does not introduce accepted calls.
- Recovery instructions and continuation notices can be added by the owning flow; they are not automatic copies of all diagnostics.

Before sending normal conversation input, each included accepted call must have its corresponding result, in provider-valid order. A view built while a batch is active can be inspected, but is not yet necessarily sendable. The simple message view is therefore not a substitute for complete context projection.

To find pending calls, track accepted completed-response calls, their optional cell starts and any terminal results. Keep the containing response association even for calls not yet started; Context needs it to retain whole batches across reset.

## Restart and interrupted batches

Startup restores records, not objects or external state. Close previously unfinished cells as interrupted, preserving parent/child relationships, then resolve accepted calls that never started with dispatch errors. Explain that effects of interrupted executions may be unknown and require inspection. Do not invent lost output, missing provider responses or completed work. Repeated startup must not add duplicate results.

For example, a process may stop while writing a report, before recording the cell's outcome. The report might be complete, partial or absent; when work resumes, the agent inspects the file before deciding what remains to do. A missing result does not establish that nothing happened.

A model step interrupted during dispatch similarly resolves only its own unanswered calls, without executing them. Nested-step cleanup must leave active parents and unstarted siblings in enclosing batches alone. Scope cleanup to the operation that ended; startup has no surviving live operations and can repair all historical pending work.

Neither loading nor repair resends a request, replays source or assumes external effects were rolled back. An unanswered request may have reached the provider; an unfinished cell may have acted externally. Evidence should expose this uncertainty, not hide it behind an automatic retry.

See [Context](context.md) for selection and reset, and [Recovery mode](recovery.md) for the repair flow.
