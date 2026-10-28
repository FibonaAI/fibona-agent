# Behavioral properties

The [design](README.md) has observable consequences when work succeeds, fails or changes direction. These scenarios describe the relationships that remain stable across those transitions.

## REPL and execution

| Scenario | Evidence |
| --- | --- |
| Define, inspect and replace a binding across cells | Persistent objects and subsequent real callers see the replacement |
| Start a language entry point from another working directory | Launcher evaluates its sibling agent source once and closes the REPL on normal or error exit |
| Await a child, then attempt overlapping competing execution | Sequential nesting works; competition does not execute or disturb capture |
| Fail parsing or mutate state then throw | Error reaches the right boundary, source is inspectable, partial effects remain, later execution works |
| Display inside nested cells and outside cells | Text persists with the correct session/cell ownership, remains visible in the console and trace, and never enters model input |
| Produce parent/child observations and user display | Nested observations are separate; display bypasses model capture; accepted model text appears once before tools, including the final reply; reasoning and retry attempts are not displayed; trajectories label each output audience |
| Change a global session binding during a cell/batch | Started operations and accepted calls remain associated with the originating log |
| Reject an invalid budget; expire a short cell budget | Invalid input has no effects; expiry reports an error with available evidence; later cells remain usable |
| Expire child versus parent deadline during nesting | Child expiry can be observed; parent expiry reaches its owner; no stale timer affects later work |
| Cancel a cell, request or recovery | Stop signal propagates, resources unwind, no recovery/fallback starts for cancellation |
| Fail start/end recording | No execution without a start; no claimed recording success or replay after an end failure |

Deadline behavior is limited by platform interruption support. Native code and child resources may continue beyond a cell deadline; detached work falls outside the managed cell lifecycle.

## Model, session and context

| Scenario | Evidence |
| --- | --- |
| Fail request recording, change settings during a request, or restart a persisted session without a recorded outcome | A failed start prevents sending; the pre-send configuration snapshot stays unchanged; an absent outcome remains unknown and startup does not resend the request |
| Receive incomplete/failed output with apparent calls | No partial effects; bounded policy retries or propagates; attempts remain inspectable |
| Receive malformed calls or unsupported arguments | No ambiguous pairing or invalid execution; identifiable rejected calls receive errors |
| Receive opaque provider items or optional metadata | Accepted data remains usable through storage and subsequent projection |
| Receive complete empty output or text plus calls | Empty output completes; calls execute in order and continue the step |
| Reset during either sibling in a response batch | Whole batch and both results remain paired, including completed siblings |
| Reset with/without handoff, then fail reset persistence | No automatic model call/summary or live-state loss; failed reset does not change the boundary |
| Inspect large output through Context | View may shorten; full evidence remains retrievable |
| Approach/exceed request budget | Selected policy advises maintenance or enters recovery; request guidance is included in cost |
| Use measured usage after reset or recovery | Old/recovery usage does not inflate the fresh normal view |
| Restart after partial batch execution, then restart again | No effects replayed, no duplicate repairs, uncertainty visible, no false claim of restored objects |
| End a nested step while an outer batch remains active | Cleanup resolves its own batch and leaves enclosing work intact |
| Load an unsupported or damaged stored log | No silent overwrite/migration; only the chosen format's justified tail repair occurs |

## Agent behavior and self-modification

| Scenario | Evidence |
| --- | --- |
| Maintain task notes through reset and recovery | Notes survive in memory, can be revised, and appear in model input only through explicit selection/output |
| Restart with persisted history | Task notes/live objects are not silently restored; continuation guidance makes the distinction clear |
| Break normal Context or loop | Independent recovery input can inspect the same environment and return to the original turn |
| Repeat recovery, succeed, fail, or fail fallback | Each recovery starts fresh; success need not reset; ordinary repair failure uses one fallback; fallback failure propagates |
| Switch session during repair | Diagnostics retain ownership; resumed normal work uses a matching session/Context pair |
| Replace a capability and deliberately fail its check | Rollback uses a preserved independent path, without another model request |
| Rebind a library name during ordinary task work | Under the target's documented dependency strategy, foundational helpers remain usable; policy replacements still reach real callers |
| Run multiple piped/interactive turns and EOF | Input is preserved once per turn, state persists and resources close |
| Inspect a fresh prompt | Discovery pointers resolve; state lifetimes, output channels, deadlines and self-modification guidance match actual capabilities |
