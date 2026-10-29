# REPL

Part of the [architecture](design.md). The REPL provides persistent editable execution. The [agent](agent/README.md) adds model access, recording, context, presentation and recovery.

## Responsibilities

The first cell defines the complete agent. Later cells share its bindings and objects, use libraries and replace definitions that subsequent real callers can observe. Isolated commands or fresh processes per cell do not provide this capability.

A minimal conceptual interface is:

```text
create environment
bind names to values
await execute(source)
read accepted source history
close environment
```

Before the first cell runs, raw execution and source history are available through discoverable bindings. History includes the complete source currently under evaluation, so the agent can record its own source without launcher injection. Accepted source that fails parsing or execution remains inspectable.

A binding associates a name with a value or object. Each REPL has its own namespace: binding a name there affects subsequent code in that environment, without changing the same name in another REPL. Code still has access to the facilities of its process.

## Sequential nesting

The execution structure is sequential nesting:

```text
first-cell agent awaits work
  → work awaits cell A
      → A awaits cell B
      → A resumes after B
```

Each active cell belongs to one nested execution chain. A cell in that chain can await a child; a competing cell is rejected before its code runs rather than queued for later execution. When a cell finishes or unwinds, execution ownership returns to its caller. The REPL does not schedule cells in the background.

The REPL accepts source only and has no wrapper hooks for agent policy. The agent independently guards its recording and shared capture before invoking raw execution, releasing that guard after its wrapper unwinds.

Independent I/O may run concurrently inside a cell, provided the cell awaits completion and manages cleanup. Detached work falls outside this lifecycle: it can outlive the cell and escape its capture or cleanup. Execution ownership can be represented by tasks, frames or another platform mechanism; its purpose is to preserve sequential nesting and prevent overlapping competition.

Sharing execution context does not by itself establish membership in the active nested chain. Implementations must distinguish sequential nesting from competing calls.

The first cell stays active while its interaction loop runs. It is agent source, not a recorded model tool call. A first tool cell can therefore have no recorded parent even though its raw evaluation is nested. REPL execution frames, source-history positions and recorded cell identities have different roles and need not share an index.

## Errors, cancellation and lifetime

Raw execution raises syntax and execution errors to the awaiting caller. Partial changes remain. The REPL neither logs session facts nor converts exceptions into model tool results.

A final expression value is optional; explicit output is the portable observation path. Cancellation supported by the platform propagates through nested calls. Errors and supported cancellation restore execution ownership and evaluator state, leaving the environment available for subsequent execution.

The launcher closes REPL-owned resources after evaluation unwinds, including exceptional exits. Agent-owned clients and readers are cleaned up by the agent.

Cell deadlines are an [agent execution policy](agent/execution.md#cell-deadlines). The REPL may supply interruption primitives, but does not choose the budget or start recovery. Neither layer promises to interrupt arbitrary native code or undo external effects. Process termination cannot be repaired inside the terminated agent.

## Language expression

Persistent objects, library access, replaceable definitions and asynchronous nesting form the execution model. An interpreter or incremental compilation session can provide that model; separate command processes generally lose shared objects and the ability to change the running agent.

[Behavioral properties](validation.md) illustrate observable outcomes.
