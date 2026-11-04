# Python implementation

These notes describe this implementation’s mechanisms, limits and verification focus. They are not requirements for other languages. See the [shared design](../docs/README.md) and [project README](../README.md) for setup.

## Managed external commands

For computation that needs a terminable boundary, keep coordination and useful state in the REPL and execute the computation as a managed external command. The Python agent provides `run_process` as an ordinary callable, not another model tool. It accepts an argument vector, an optional positive timeout and working directory/environment overrides. An enclosing cell deadline still cancels it, so its budget cannot extend that deadline. Output is returned for deliberate inspection rather than injected into context.

On POSIX, each invocation owns a new process group. Completion, timeout and cancellation clean up that group with bounded termination and kill waits, retaining captured output. A process timeout becomes an execution error with output and command diagnostics; external cancellation still propagates. Cleanup failure is reported, not represented as successful termination. Processes that leave the group and uninterruptible operating-system operations remain outside this guarantee. No process restart or restoration of live objects is implied, and arbitrary commands started outside this capability are not automatically adopted.

Verify timeout of a process pool and a command with grandchildren (including a child that ignores termination), bounded cleanup, retained partial output, nonzero exit diagnostics and cancellation. Verify that nested cell budgets cannot be extended, unrelated processes survive, and the next cell can use unchanged live state. A normal command must also leave no running members of its owned process group. Unsupported platforms and cleanup failures must be explicit rather than reported as successful cleanup.

## Execution and bindings

Python asynchronous cancellation is delivered at supported waiting points; some synchronous work requires additional platform facilities. The executor associates ownership with the current asynchronous task and rejects competing tasks. The agent wraps the source-only raw executor with its own sequential guard, recording, output capture and deadlines, rejecting competing agent calls before touching records or shared streams.

Python functions resolve global names in their defining namespace when called, so rebinding a helper, such as the function that builds model input, affects later calls; callers retaining the old reference keep using it. Function-local imports reduce the effect of incidental name reuse on recording, error reporting and transport. Policy bindings such as prompt, context, tools and loops remain deliberately editable. Task-code `SystemExit` is captured as an ordinary cell error rather than terminating the agent; external cancellation retains its control meaning.

## Tools and storage

The initial catalog lists `run_process`, `view_image`, `display` and native `input`. Only `run_cell` is exposed to the model API. Its recorded result automatically supplies output, error, status, elapsed seconds and declared timeout to the model; return values of other callables require explicit printing or display. See [cell execution timing](../docs/agent/execution.md#cell-deadlines) for measurement boundaries and unknown historical durations. A readable copy of the prompt is [prompt.md](prompt.md); requests use the live `instructions` in `agent.py`.

`view_image` accepts paths, bytes, image URLs and PIL-like objects. It records an image observation for subsequent model requests; for example, `view_image("chart.png", detail="high")`. The provider handles decoding and resizing. Data URLs longer than the Responses API's 20,971,520-character limit are rejected.

Before measured usage is available, the default GPT-6 Astra estimator reserves 308 tokens per `low` image, 3,000 for `high`, and 36,000 for `auto`/`original` (including omitted detail), based on the [provider's image limits](https://developers.openai.com/api/docs/guides/images-vision). These are conservative budgets, not exact counts. Later estimates use measured input/output usage plus new observations and prompt/tool growth. Base64 and encrypted reasoning are excluded from text-length estimates. Adjust this policy when changing models.

Sessions use single-writer versioned JSONL. The loader validates the header, record identities, associations and complete records, and rejects incompatible formats and interior corruption without overwriting them. Lone surrogates in text, such as undecodable file names, are stored as JSON escapes and load back unchanged. Only an unparsable unfinished final fragment after a valid prefix is discarded; a valid final record lacking a newline is preserved. `session.records` holds the stored facts themselves, so treat it as read-only; projected model input is a separate copy.
