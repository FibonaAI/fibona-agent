# Design overview

These documents define the design of REPL Agent: its purpose, the relationship between REPL and agent, and the agent's state, components and behavior. They provide the basis for implementing and evaluating language versions without relying on an existing implementation to explain the design.

This page introduces the overall idea and links to each part. Setup and usage are in the [project README](../README.md); instructions for maintaining the documentation and code are in [AGENTS.md](../AGENTS.md).

REPL Agent explores an agent whose working environment includes the agent itself. Its instructions, model access, tools, context policy and work loop are ordinary code in a persistent REPL. A REPL evaluates successive blocks of code, called cells, while retaining variables and functions between them. The code the agent writes runs alongside its own definitions, with access to the same objects and resources.

The environment is both a means of doing the user's work and something the agent can understand and change. It can inspect how it selects context, try a different policy, check the result and retain or undo the change. Self-modification is part of the ongoing task: its value comes from helping accomplish the user's goal.

## Execution and agency

The system has two main parts. The **REPL** keeps an execution environment alive: names remain bound, objects survive between cells, asynchronous work can be awaited, and nested execution returns to its caller. The **agent** gives that environment a way of working: it requests model responses, executes selected actions, keeps evidence, communicates with the user and handles failures.

The complete agent is evaluated in the first cell. Later task cells live in the same namespace and process. This makes the agent's behavior accessible through the same code-execution capability it uses for ordinary work. The REPL supplies continuity of execution; the policies governing that execution live in the agent.

## Four views of ongoing work

| Concept | What it represents |
| --- | --- |
| Execution environment | Variables, functions and resources retained in the REPL for subsequent code to use |
| Task state | Editable notes about the goal, progress, open questions and where to find supporting information |
| Session | The accumulated record of inputs, model responses, executed source and observations |
| Context | The selected information presented to the model for its next decision |

These roles have different lifetimes. A context reset changes what the model sees without erasing history or live objects. Task notes can be revised while historical facts remain intact. A process restart can recover persisted evidence, but does not reconstruct the old live environment automatically.

The model sees selected information, not the entire REPL. Task state and other objects become visible through deliberate inspection and output. The agent can therefore keep substantial working material outside its immediate context and retrieve what matters for the next step.

## Work, repair and change

Normal work follows a repeated cycle: select context, request a model decision, execute complete tool calls, record observations and consider the next step. Code execution is the model's single tool; other capabilities are available within that code. Observations returned to the model and messages displayed to the user serve different purposes and use separate channels.

When the work loop fails, recovery pauses it and approaches the fault with fresh input. Recovery shares the live environment and evidence, so it can inspect and repair the same state. It then returns to the original task. Repair does not erase earlier effects or prove that the next attempt will succeed.

The same principle governs deliberate self-modification. The agent preserves a working path, changes a definition and checks the behavior through its actual callers. A successful assignment alone says little about improvement: dependencies may still refer to old code, active calls may still be running, and external effects remain.

## The shape of the design

Persistence, inspectability and editability make the agent's own ways of working available for improvement. They also make state ownership important. A call and its result stay associated; history remains evidence; context changes do not replay actions; cancellation ends work. Cell budgets bound execution where the platform supports interruption, without implying rollback or supervision of every child resource.

These relationships define the project across languages. Evaluators, libraries, storage formats and policy parameters can vary while preserving the same model of work. The environment shares the capabilities and limits of its host process; it does not provide sandbox isolation or guaranteed recovery from arbitrary damage.

## Explore the design

[Project design](design.md) explains the REPL/agent relationship and how the launcher connects them. [REPL](repl.md) describes persistent execution. [Agent design](agent/README.md) follows normal work, recovery and self-modification through their components:

- [Recovery mode](agent/recovery.md): independent repair input, fallback and continuation of the original task.
- [Model access and execution](agent/execution.md): requests, actions, deadlines and output audiences.
- [Session](agent/session.md): evidence and the relationships between recorded events.
- [Context](agent/context.md): selection, continuation and budget management.
- [System prompt](agent/system-prompt.md): purpose, working principles and environment awareness.

[Behavioral properties](validation.md) provide scenarios and expected outcomes for checking an implementation against the design.

## Documentation boundaries

This directory describes language- and implementation-independent concepts, responsibilities, behavior and acceptance criteria. Implementations maintain file layout, concrete interfaces, storage encodings, platform limits and implementation-specific checks as needed; see [Python](../python/README.md) and [Node.js](../node/README.md). Implementation notes must not become implicit requirements of the shared design.
