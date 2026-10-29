# Node.js implementation

These notes describe this implementation’s mechanisms, limits and verification focus. They are not requirements for other languages. See the [shared design](../docs/README.md) and [project README](../README.md) for setup.

## Cooperative cancellation

The Node implementation uses cooperative `AbortSignal` cancellation. `sleep` and model requests observe the active signal; other abortable I/O receives `cell_signal()` explicitly. An external caller can supply a signal to `run_cell`. Child calls inherit the enclosing signal, so a parent expiry reaches the parent boundary. Only an aborted cell signal marks interruption; an `AbortError` raised by task-local controllers is an ordinary cell error. Expiry becomes a cell result only after evaluation unwinds. A timer cannot run while the event loop is blocked, so blocking JavaScript or a promise that ignores the signal can delay that boundary indefinitely; the executor keeps ownership and capture instead of claiming completion while code continues in the background.

Exercise deadlines and external cancellation with signal-aware waits. A competing sibling must be rejected before it changes capture; verify that the parent can still print after the rejection. An uncooperative operation must not be reported complete before it really exits.

## Execution and bindings

Asynchronous context can also reach separately started siblings, so sharing it alone does not establish membership in the active nested chain. The agent wraps the source-only raw executor with its own sequential guard, recording, output capture and deadlines, rejecting competing agent calls before touching records or shared streams.

Lexical declarations, reassignable variables and module exports behave differently when rebound, and callers retaining an old function reference keep using it; check replacements, such as the function that builds model input, through the actual calling path. Dependencies retained in local scopes reduce the effect of incidental name reuse on recording, error reporting and transport. Policy bindings such as prompt, context, tools and loops remain deliberately editable.

## Tools and storage

The initial catalog lists `end_turn`, `display`, `input`, `sleep` and `cell_signal`. Only `run_cell` is exposed to the model API. Its recorded result automatically supplies output, error, status, elapsed seconds and declared timeout to the model; return values of other callables require explicit logging or display. See [cell execution timing](../docs/agent/execution.md#cell-deadlines) for measurement boundaries and unknown historical durations.

Sessions use single-writer versioned JSONL. The loader validates the header, record identities, associations and complete records, and rejects incompatible formats and interior corruption without overwriting them. Only an unparsable unfinished final fragment after a valid prefix is discarded; a valid final record lacking a newline is preserved. `session.records` holds the stored facts themselves, so treat it as read-only; projected model input is a separate copy.
