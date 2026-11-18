# Terminal bootstrap v2

This bootstrap keeps request memory explicit and cache friendly. The session exposes a mutable `context` list containing Responses API input items. The agent decides what belongs there and whether to send it on each call:

```python
context.append({"role": "user", "content": "The API uses UTC timestamps."})
response = call_me(input=[*context, {"role": "user", "content": "Continue."}])
```

The agent maintains `context`: it should review `history` and append only durable facts, decisions, results, or
pending work that need to survive into later calls. A call can omit the list and send only its latest observation.
The bootstrap places the session instructions at the beginning of `context`; keep that stable prefix unchanged and
append new items at the end so the API can reuse the cached prefix.
Because the model has a finite context window, compress older context into concise summaries before it approaches
the limit. Preserve the instructions and current goal, and remove redundant code, output, and intermediate details.
The host does not add history, variables, or task state automatically.

This is an experimental replacement for `bootstraps/terminal`. The original bootstrap is unchanged.
