# new_core

An experiment in agents that can inspect, modify and improve themselves. The complete agent—including its prompt, session, context and work loop—runs in the first cell of a persistent REPL. Later cells share the same live environment.

Python and Node implementations are included. This experiment does not replace the repository's original `core/`, `bootstraps/` or `fibona.py`.

## Run

From the repository root, configure `.env` with `OPENAI_API_KEY`. Set `OPENAI_BASE_URL` for a compatible Responses API service and `FIBONA_MODEL` to select a model.

```sh
# Python 3.12+, using the project's dependencies
uv run --env-file .env experiments/new_core/main.py

# Node 24+, no npm dependencies or build step
node --env-file=.env experiments/new_core/main.js
```

Both support multi-turn conversations and piped input. Sessions are saved under `~/.local/state/fibona/sessions/`. Set `FIBONA_SESSION` to choose or resume a file, or set it to an empty string for an in-memory session. Use separate files for each running instance and platform. Resuming history does not restore live objects or replay code.

Each launcher accepts `--runtime` and `--bootstrap`. A bootstrap can be a built-in name, directory or source file compatible with the runtime:

```sh
node --env-file=.env experiments/new_core/main.js --runtime node --bootstrap ./my_bootstrap.js
```

Model request defaults are grouped at the top of each bootstrap in `model_options`; `context_window` sets the local context budget. Check both when changing models or providers.

## Architecture

```text
Launcher                 Select runtime, read bootstrap, execute it
  └─ Runtime             Maintain a persistent REPL and execute cells
       ├─ Cell 0         Complete bootstrap: agent code and prompt
       ├─ Cell 1         Model-generated code
       └─ Cell 2 …       Continue in the same live environment
```

The launcher and runtime do not own agent policy. The bootstrap owns model access, history, context, interaction and recovery. Runtime and bootstrap can be selected separately or fixed together for a device; platform-specific code and instructions belong with the bootstrap.

The agent works with four composable capabilities:

| Capability | Purpose |
| --- | --- |
| `request_model` | Request a response with explicit input |
| `run_cell` | Execute and record code in the shared REPL |
| `session` | Append facts about what happened |
| `context` | Build model input from history; reset with an optional handoff |

Only `run_cell` is a model-facing tool. Generated code can directly use the other capabilities.

## Design principles

- **The agent can understand and change itself.** Bootstrap source and later execution history are available through session. Prompts, functions and working state are editable in the REPL. Changes should be verified with a local rollback ready; live edits do not automatically change files on disk.
- **History and context are separate.** Session preserves facts; context selects a limited working view. The agent can start fresh context, optionally carrying its own handoff, without losing history or live state. There is no automatic model-generated summary.
- **Output has an audience.** `print` / `console.log` writes model-facing output, recorded and returned through tool results. `display` communicates with the user without feeding rendered values into context. The cell source, including display calls, remains recorded.
- **Recovery reuses the environment.** If the work loop fails, a separate context can inspect and repair the shared REPL, session and paused task context before work resumes. If repair fails, normal work gets fresh context with pointers to the failure and history. Recovery depends on usable execution, model access and storage; it cannot recover a terminated process.
- **Keep the host small.** Prefer ordinary editable code over registries, orchestration frameworks or extra abstraction layers. The REPL is not a sandbox, and failed execution may leave partial effects.

## Inspecting a run

Sessions are JSONL files containing the bootstrap source, model requests and responses,
cell execution, mode transitions and context resets. Use them to follow progress,
inspect failures and distinguish normal work from recovery. Request records describe
logical model calls, not individual transport retries inside an SDK.

The harness allows three response attempts before recovery. Python's `api_retries`
controls SDK transport retries separately; those internal attempts are not logged.

A session is an execution history, not a checkpoint of live memory. It does not capture
every external effect or guarantee exact replay. For the record format and derived
views, see the [Python bootstrap](bootstraps/python/main.py) or
[Node bootstrap](bootstraps/node/main.js).

## Development

```text
main.py / main.js          Launchers
runtimes/python/          IPython runtime
runtimes/node/            Node REPL runtime
bootstraps/python/        Python agent and prompt
bootstraps/node/          Node agent and prompt
```

Python code checks:

```sh
uv run ruff check experiments/new_core
uv run ruff format --check experiments/new_core
```

See [AGENTS.md](AGENTS.md) for maintenance constraints and verification guidance. Interface details live in the code and its comments.

## Harbor evaluation

Copy [eval/example.yaml](../../eval/example.yaml) to a private job YAML and select
`agent: plugins.new_core:NewCore`. Set `model`, optional `base_url`, and
`model_settings` for the chosen model; check its context window and output limit.
Export `OPENAI_API_KEY` and configure workers and archive as described in the
[eval README](../../eval/README.md), then run:

```sh
make -C eval job CONFIG=/path/to/job.yaml
```

The Python bootstrap accepts `FIBONA_REASONING_EFFORT`, `FIBONA_CONTEXT_TOKENS`,
`FIBONA_MAX_OUTPUT_TOKENS`, `FIBONA_API_RETRIES` and JSON `FIBONA_EXTRA_BODY` as
startup defaults. Explicit eval YAML settings override these values. These additional
settings currently apply to Python; standalone use does not require eval.

Trial artifacts are collected in `eval/experiments/<run>/jobs/<run>/<trial>/agent/`:
`session.jsonl` (full recorded history), `console.txt`, `bootstrap.py`, `instruction.txt`
and `metrics.json` (usage and request latency). Missing usage remains unknown rather
than zero. These files enter the job archive after credential redaction; earlier
attempts are discarded on retry as described in the eval README.
