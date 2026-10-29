# System prompt

Part of the [agent design](README.md). The prompt gives purpose, working principles and discovery paths into the live environment. It is editable agent source, shared by normal work and recovery.

## Organization

The prompt separates eight concerns, allowing the agent to inspect and revise one aspect of its guidance without losing the others:

| Section | Guidance |
| --- | --- |
| Identity | The agent's purpose and intent to improve its ways of working |
| Working | Advance the task through evidence, action and verification |
| Environment | Understand the agent's composition, shared runtime and capacity for self-modification |
| run_cell | Write code, manage execution and resources, and choose output audiences |
| Session | Retrieve evidence and distinguish historical facts from current state |
| Context | Actively maintain model input, task state and continuity |
| Tool | Discover, use and maintain tools through the catalog |
| Reflection | Investigate discrepancies and evaluate changes to approach or environment |

Sections express durable concepts and judgment principles rather than a growing checklist of past failures. Keep concrete execution conventions in tool interfaces and callable documentation, with discovery paths from the prompt.

Each request uses the current guidance. Edits affect future requests, not those already sent. Recovery adds a repair instruction to its own input rather than replacing the agent with another persona.

## Working and reflection

Teach the agent to establish goals, constraints and completion evidence; work independently while useful progress is possible; and distinguish assumptions, attempts and verified outcomes. Unexpected results should stop dependent work and trigger inspection rather than blind repetition.

Choose actions that advance the user’s goal or resolve uncertainty that blocks progress. Carry the task through to completion and use checks that distinguish explanations and can disprove assumptions. Verify actual deliverables rather than treating a completed procedure as success. Preserve useful progress and report unresolved uncertainty.

Reflection can change understanding, plans, tools, state organization, context, prompt or the work loop. When testing an uncertain improvement, choose an experiment that can test the hypothesis and limit its effects. Adding a permanent rule is not the default response to one incident. Preserve conditions and evidence for lessons, apply them, and evaluate whether they help.

## Environment and component sections

Environment gives the agent a model of itself: the complete agent is first-cell code whose instructions, model access, execution wrappers, tools, session, context policy and work loop share a live namespace and process with task code. It keeps concrete discovery pointers to the first-cell source, `instructions` and `request_model`, and explains why a changed binding affects future lookups but not active calls or retained references.

The run_cell, Session, Context and Tool sections each give judgment guidance for one component. Signatures and execution details stay in the callable documentation they point to. Two distinctions must survive edits: context reset differs from process restart, and failure or rollback does not undo external effects. Reflection owns how self-modification is judged; the rules for changing capabilities needed to keep running are in [self-modification](README.md#self-modification-and-dependency-stability).

Implementations share the general guidance and section order; only language and runtime differences need distinct wording.

## Guidance in the live environment

Prompt sections carry durable working principles. From the discovery pointers, the agent can inspect the code that provides its capabilities and learn how to use them. Changing task progress stays in working state and history, so it does not become permanent instruction.

Instructions guide use of execution boundaries, recording and context policy; code must implement those mechanisms. When changing guidance, inspect the composed prompt for contradictions, preserve the old version and verify the next real request path. Remove obsolete guidance rather than accumulating patches.

The run_cell section states when to call `end_turn(message)`: after the checked final result, or in recovery once normal work can resume ([explicit end of a turn](execution.md#explicit-end-of-a-turn)). Process exit is never normal completion.
