# Maintaining this experiment

These instructions cover maintaining the design, documentation and language implementations in this directory. They are for the agent doing that maintenance, distinct from the running agent's cell-0 prompt, and are not automatically sent to the model.

## Read before making changes

Start with the [design overview](docs/README.md), then read the [architecture](docs/design.md) and the [agent flow and component map](docs/agent/README.md) or [REPL contract](docs/repl.md) relevant to the change. For a new language implementation, read the complete design and [acceptance scenarios](docs/validation.md). Use the [README](README.md) for supported setup and user-facing behavior. For a new implementation, derive the plan and tests from the design. Do not use existing source or tests as hidden requirements. Resolve missing architectural decisions in the design; make ordinary implementation choices locally. When changing existing code, inspect the affected source and tests after identifying the applicable design contract.

The design is the specification from which code and acceptance tests are produced; this file defines the maintenance workflow. Design pages supply intent, ownership, essential invariants and failure boundaries. Distinguish requirements from reference policies and local choices; they need not specify every field, signature or validation mechanism. Source comments explain language-specific realizations. If code disagrees with the design, investigate an implementation defect or a documented limitation; change the design only when the requested scope includes a deliberate design decision.

## Scope and change discipline

- Make only task-relevant changes. Preserve unrelated concurrent edits and other experiments; do not replace the repository's original entry point.
- Prefer a few composable, language-native capabilities. Do not add registries, common frameworks or platform abstractions without a concrete task requiring them.
- Keep work policy in the editable agent, following the ownership boundaries in the design. Do not solve isolated model mistakes with host policy or accumulating prompt patches.
- Before implementation, identify the goal, affected contracts and observable completion criteria. For multi-step work, use a short sequence of implementation steps and verification evidence.
- For shared semantic implementation changes, inspect both current implementations after resolving the documented contract. Documentation sufficiency reviews must not use either implementation as an oracle. For platform-specific changes, adapt to that platform rather than mechanically porting details.
- If a requested change alters architecture or required behavior, first update the design decision and acceptance criteria, then implement and verify that decision as part of the same logical delivery. Do not broaden the task to fix unrelated gaps.

## Source map

| Concern | Location |
| --- | --- |
| Startup | `python/main.py`, `node/main.js` |
| Persistent execution and nesting | `python/repl.py`, `node/repl.js` |
| Complete agent, prompt, model settings and interaction | `python/agent.py`, `node/agent.js` |
| Behavior tests | `python/tests/test_core.py`, `node/tests/node.test.js` |
| Architecture and behavioral contracts | `docs/design.md` and its linked pages |

## Implementing another language

State the target language/version, execution engine, asynchronous execution and cancellation mechanism, model transport, session storage and interaction facilities. Verify that the language can support the REPL contract before building the full agent. Report a capability gap instead of presenting a command runner as an equivalent persistent environment.

Keep each language implementation in its own directory, with `main.*`, `repl.*`, `agent.*`, local dependencies and `tests/`. Keep shared documentation at the experiment root. Implement and verify in this order:

| Step | Deliverable and evidence |
| --- | --- |
| REPL and minimal launcher | First-cell agent execution, persistent bindings, nesting, error propagation and cleanup |
| Complete agent and recorded execution | Inspectable source, task state, correct cell associations, separate output audiences, deadlines and stable session ownership |
| Session and projection | Append-only persistence, derived results, restart repair without replay |
| Context and model steps | Explicit input, complete batch pairing, budget policy and safe response retries |
| Work, recovery and self-modification | Independent recovery input, owned fallback and replacement through actual callers |
| Platform integration | Interaction, transport serialization, resource cleanup and documented differences |

Deliver REPL and agent code, launcher integration, relevant tests, run instructions and known platform limitations. Equivalent capabilities and correctness are the criterion. Different reference-policy constants, representations, function names or source structures are allowed when they preserve the design. Do not convert comparison-test differences into requirements without a design reason.

## Verification

Run commands from the repository root. Choose checks relevant to the change and use the design's acceptance scenarios to select behavioral coverage.

```sh
# Python style checks
uv run ruff check experiments/repl_agent
uv run ruff format --check experiments/repl_agent

# Python behavior tests
uv run python -m unittest discover -s experiments/repl_agent/python/tests -p 'test_*.py'

# Node behavior tests (Node 24+)
node --test experiments/repl_agent/node/tests/node.test.js
```

Check both platforms when changing shared semantics. When changing a REPL or interpreter version, specifically verify persistent definitions, nesting, exception routing and cancellation. Use the actual caller path to verify self-modification, not only assignment or a direct call to a candidate helper.

For design-only changes, verify internal consistency, interface completeness, failure paths and acceptance coverage. Existing code may suggest a deliberate design update when that is requested; explain the purpose of the adopted decision instead of copying its mechanics. Otherwise use code comparison to identify gaps, not hidden requirements. Verify operational commands and implementation-status claims against their actual sources, and check document links; no paid model calls are needed. Do not modify implementation merely to make a documentation pass look broader.

Use temporary sessions and deterministic responses or local transport servers for offline behavior tests. Use separate temporary sessions for live model checks and retain inspectable execution evidence. Do not modify repository source or user sessions during live checks. Report offline tests and live requests separately; mocked responses are not live model validation. Report failed or unavailable checks and unsupported semantics explicitly.

## Current implementation gaps

The following are source-inspection findings to account for when changing related code; existing tests do not establish full design coverage:

- Node lacks the dedicated task-state binding and cell deadlines present in Python. Its recorded cell executor also converts cancellation into an ordinary error result.
- Both agents still read mutable session globals during batch dispatch and recovery. Pending-call cleanup is not scoped to a response batch, and mode filtering follows chronology rather than operation ownership.
- Recording failures are not consistently distinguished from recoverable loop failures. Context warning text is added after the budget estimate.

Fix and verify these within the relevant implementation task; remove notes as the gaps are resolved. Concrete APIs and storage details are documented alongside their source.

## Interpreting and checking the design

The design is the authority for intended project behavior. Its subject is the system itself: what it is, why its concepts exist, how they relate and what happens as work proceeds. Write design pages from that perspective. Keep implementation instructions, documentation governance, delivery checklists and review procedures in this file. Code generation is a use of the design, not its narrative subject.

Distinguish essential relationships and correctness requirements from reference policies and implementation choices. Retry counts, field names, signatures and storage encoding need not match across languages unless compatibility is explicitly required. Precision is useful when different interpretations could lose evidence, duplicate effects, mix sessions or break recovery. Ordinary unspecified mechanics can be chosen locally.

To check documentation sufficiency, hide existing source/tests and derive an implementation and behavioral tests from the design. Classify differences as a missing/conflicting design decision, an implementation defect, a permitted choice or a capability limitation. Compare effects, state lifetimes and operation ownership rather than incidental transcripts. Existing tests cannot create hidden requirements. Documentation review and successful independent construction are different evidence; report them separately.

## Documentation maintenance

For a documentation task, identify the reader, the question the page answers and the design decision or usage it describes. Review related pages together: a change to recovery, for example, may affect the agent flow, Session, Context and behavioral examples. Check that they describe the same behavior. Address the underlying issue across relevant pages rather than patching only the sentence that exposed it.

Maintain one home for each kind of information:

| Document | Audience and content | Update when |
| --- | --- | --- |
| `README.md` | People understanding and using the experiment: purpose, quick mental model, setup, operation, configuration and evaluation | Supported usage or user-visible behavior changes |
| `AGENTS.md` | Coding agents maintaining this directory: workflow, source map, verification and documentation rules | Maintenance workflow, layout or check commands change |
| `docs/` | A natural-language model of the project: purpose, concepts, relationships, behavior, rationale and boundaries | The project design changes |
| Source comments | Readers of a specific implementation: concrete signatures, local mechanisms and platform details | The corresponding implementation changes |

English is the canonical design text; resolve translation conflicts by updating the corresponding Chinese text. Maintain an English document and a complete Chinese counterpart named `*.zh.md` for every Markdown document in this directory. Keep both versions aligned when changing content, preserve code identifiers and executable commands, and link each version to the other. Chinese pages should link to Chinese counterparts where available. `AGENTS.md` remains the maintenance entry point; `AGENTS.zh.md` is its translation, not a separate set of rules.

Organize the design in two levels: `docs/design.md` explains the REPL/agent relationship, their interface and launcher integration; `docs/agent/README.md` owns the agent’s internal state, work/recovery flows and component design. Keep detailed REPL and acceptance contracts as supporting pages. Keep this navigation and cross-references current. Link to the owner of detailed information instead of copying the same rules into the README, these instructions and the prompt. Keep transient test counts, temporary logs and isolated model errors out of long-lived documentation.

Keep `docs/README.md` explicit about the purpose of the documentation and the reading path. Before completing documentation changes, read the affected English and Chinese pages together, check links and section anchors, and trace the described normal and failure paths for missing or conflicting decisions. State which checks were performed; a static document review does not establish that an independent implementation succeeds.

For a new language, document consequential language differences in the relevant REPL or agent design section. Add target-specific design only for substantial decisions or explicit compatibility contracts; ordinary choices do not require a separate specification before coding. Record unresolved implementation gaps under “Current implementation gaps.”

Use the direction: design decision → acceptance criteria → implementation → verification. Do not rewrite the specification merely to describe the resulting code. Finish a change with the code, applicable tests and affected documentation aligned. Report what changed, the evidence checked and remaining limitations; do not describe unverified behavior as completed.
