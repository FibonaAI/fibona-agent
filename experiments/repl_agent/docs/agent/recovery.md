# Recovery mode

Recovery mode gives the agent a way to repair a failure that prevents normal work from continuing. It pauses the task's work loop and asks the model to inspect the failure using a separate working context. The repair runs in the same REPL, with access to the original task, history and current variables.

For example, the agent may replace its Context policy and introduce a bug. The next request cannot be prepared because that policy now raises an error. Retrying the same work loop would encounter the same fault. Recovery can make a request without using that Context, inspect the replacement and restore a working definition.

## When recovery begins

Recovery begins when an ordinary error escapes the normal work loop. This includes errors in input construction, an exceeded context budget, or a recoverable model failure that persists after its response attempts. Refusal and rejections the model cannot repair stop directly. Other permanent rejections enter recovery; the same rejection of a recovery request stops ([failure boundaries](execution.md#failure-boundaries)).

An ordinary cell error has a different path: its output and diagnostics become a tool result, which the next normal model step can inspect. External cancellation ends the run. Unusable execution, model access or recording infrastructure can prevent recovery itself from operating. Initialization failures also propagate directly because the agent has not yet established a working environment for repair.

## What changes and what stays

| Part | During recovery |
| --- | --- |
| Work on the user's task | Paused until repair ends |
| REPL variables, functions and resources | Shared with normal work; repair can inspect and change them |
| Task notes | Preserved in memory; inspected explicitly as needed |
| Session | The same history, with recovery entry, actions and outcome recorded |
| System prompt and model tools | Shared with normal work |
| Normal Context | Paused; its build, shortening and budget policy are bypassed |
| Recovery input | Starts fresh for each invocation and grows with that invocation's observations |

The mode change neither restarts the process nor resets normal Context. Recovery can deliberately repair or reset that Context when necessary.

## Independent input

At entry, the agent records the failure and a repair instruction. The instruction explains what failed, points to the original task and relevant history, and directs the model to inspect current state before changing it. It also makes the immediate objective clear: repair the fault so the task can continue.

The first repair request uses that instruction. Subsequent requests include the calls and results from this recovery invocation. Earlier task history and earlier recoveries remain available for inspection rather than being copied into the repair input automatically.

This input is built directly from Session, independently of normal Context. Recovery therefore does not need the potentially damaged selection, shortening or token-estimation code. It still uses the same model access, tool dispatch and recorded cell execution, so it can repair higher-level policy only while those basic capabilities remain usable.

## Repair and return

Repair uses the shared model step to inspect definitions, read relevant history, change code or restore an earlier definition. Its effects happen in the original environment. It does not resume the user's task or call the normal work loop while repair is still in progress.

Tool calls continue repair until a successful cell commits `end_turn(message)` for this recovery invocation. The agent records the exit and tries the normal loop again for the same user turn. The user message is not appended again, and no new input is read.

Recovery observations remain in history but do not automatically enter the normal working context or its usage estimate. Successful return does not itself reset normal Context. The next normal iteration establishes whether the repaired capability actually works; a failure there can start a fresh recovery invocation.

## When repair fails

If repair fails with an ordinary error, recovery performs one fallback:

1. Record the repair failure together with the original failure.
2. Create a fresh Context, avoiding the possibly damaged instance.
3. Reset it with a handoff pointing to the original task, prior progress and failure details.
4. Resume normal work with a matching session and Context.

Fallback work may fail and start a fresh recovery. If normal work fails again after two consecutive recoveries have fallen back, the turn stops explicitly instead of cycling; a persistent fault such as broken model access would otherwise repeat without end.

The handoff provides a way to inspect and continue; it does not invent a summary of work that repair never examined. Existing objects, files and external effects remain. A failure while creating or resetting the fallback Context propagates instead of starting another repair inside recovery.

Cancellation and explicit stops bypass fallback. Recording failures also propagate, because continuing to repair against uncertain history could obscure which actions occurred.

## Continuity and limits

Requests, cells and repair diagnostics retain their originating session associations. If repair deliberately selects another session, its exit is still recorded with its original recovery, and subsequent normal work uses a Context bound to the selected session.

A context reset changes future model input; it does not roll back code or effects. A function reference saved before a change may still depend on mutated globals. Repair therefore uses the same inspection and rollback discipline as [self-modification](README.md#self-modification-and-dependency-stability).

Recovery is part of the running agent and depends on that process. It cannot revive a terminated process or guarantee repair of arbitrary damage. Repeated failures are handled through successive work-loop attempts, without accumulating nested recovery calls.

Input selection is described in [Context](context.md); execution and interruption are described in [execution](execution.md).
