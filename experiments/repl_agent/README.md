# REPL Agent

An agent that can inspect, modify and improve its own working environment. Its complete implementation, including the prompt, history, context policy and work loop, runs in the first cell of a persistent REPL. Later cells share that environment, so the agent can inspect its definitions, change them and check the results.

A REPL is an interactive environment that evaluates code while retaining variables and functions for later use. Each submitted block of code is called a cell. Python and Node implementations are included.

The [design overview](docs/README.md) explains the ideas behind this environment and how its parts work together.

## How it works

```text
Launcher → Persistent REPL → Complete agent in the first cell
                                      ↓
                              Later cells share live state
```

The REPL keeps code and objects alive between cells. The agent owns model access, interaction, history, context and recovery.

Session history preserves what happened; context selects what the model sees next. Starting fresh context leaves history and live objects available. If the work loop fails, recovery can inspect and repair the shared environment before work resumes. Recovery needs usable execution, model access and storage; it cannot revive a terminated process.

Output has two audiences. `print` in Python and `console.log` in Node provide recorded observations to the model. `display` presents progress, questions and answers to the user without automatically adding the rendered values to model context. Accepted model reply text is automatically shown to the user and retained in history; `display` is for user-facing content produced inside a cell.

Use `tool_catalog` to discover local helpers, then inspect and call their current functions. Add reusable tools after checking them; the model API still exposes only `run_cell`. The [local-tool convention](docs/agent/README.md#local-tools) covers documentation, replacement and explicit persistence.

Changes made in the live environment do not automatically update source files on disk.

## Run

From the repository root, configure `.env` with `OPENAI_API_KEY`; startup stops if it is missing. Set `OPENAI_BASE_URL` for a compatible Responses API service and `FIBONA_MODEL` to select a model.

```sh
# Python 3.12+, using the project's dependencies
uv run --env-file .env experiments/repl_agent/python/main.py

# Node 24+, no npm dependencies or build step
node --env-file=.env experiments/repl_agent/node/main.js
```

Both support multi-turn conversations and piped input. Sessions are saved under `~/.local/state/fibona/sessions/`. Set `FIBONA_SESSION` to choose or resume a file, or set it to an empty string for an in-memory session. Use separate files for each running instance and platform.

Resuming a session reloads its recorded history without replaying old code. Variables from the previous process are not restored, and the current state of files and other external resources needs to be checked. The execution environment is not a sandbox; code has access to the facilities of its host process.

### Configuration

| Setting | Purpose | Availability |
| --- | --- | --- |
| `OPENAI_API_KEY` | Model service credentials | Python and Node |
| `OPENAI_BASE_URL` | Compatible Responses API endpoint | Python and Node |
| `FIBONA_MODEL` | Model selection | Python and Node |
| `FIBONA_SESSION` | Session file, or empty for in-memory history | Python and Node |
| `FIBONA_CONTEXT_TOKENS` | Local context window used for budgeting | Python and Node |
| `FIBONA_REASONING_EFFORT` | Reasoning effort | Python |
| `FIBONA_MAX_OUTPUT_TOKENS` | Maximum response size | Python |
| `FIBONA_API_RETRIES` | SDK transport retries | Python |
| `FIBONA_EXTRA_BODY` | Additional request body as a JSON object | Python |

Model request defaults are grouped at the top of each agent in `model_options`; `context_window` sets the local context budget. Check both when changing models or providers. Other options can be changed in the agent source.

## Implementations

Each language keeps its launcher, REPL and complete agent together:

```text
python/                  node/
  main.py                  main.js
  repl.py                  repl.js
  agent.py                 agent.js
                           package.json
docs/                    Project and agent design
```

`main.*` starts the REPL and evaluates the agent source from the same directory, `repl.*` provides persistent execution, and `agent.*` contains the complete editable agent. Python uses the repository's dependencies; Node's package settings are local to `node/`.

## Inspecting a run

Session JSONL files contain agent source, model requests and responses, cell execution, mode transitions and context resets. They help trace progress and failures, including the distinction between normal work and recovery.

Request records describe logical model calls. Internal SDK transport retries are not individually logged. Response-level retries are separate; details and record relationships are described in the [agent design](docs/agent/README.md), [session](docs/agent/session.md) and [context](docs/agent/context.md).

A session is an execution history, not a checkpoint of live memory or a complete record of external effects.

## Harbor evaluation

Copy [eval/example.yaml](../../eval/example.yaml) to a private job YAML and select
`agent: plugins.repl_agent:ReplAgent`. Set `model`, optional `base_url`, and
`model_settings` for the chosen model; check its context window and output limit.
Export `OPENAI_API_KEY` and configure workers and archive as described in the
[eval README](../../eval/README.md), then run:

```sh
make -C eval job CONFIG=/path/to/job.yaml
```

The evaluation integration uses the Python agent. Explicit eval YAML settings override its environment defaults. Standalone use does not require eval.

Trial artifacts are collected in `eval/experiments/<run>/jobs/<run>/<trial>/agent/`:
`session.jsonl` (full recorded history), `console.txt`, `agent.py`, `instruction.txt`,
`metrics.json` (usage and request latency), and `trajectory.json` (ATIF for `harbor view`).
The trajectory projects model replies, tool results, nested cells and recovery diagnostics
from the native session without modifying it; incomplete replies do not become executable
tool calls. New sessions also record user-facing `display(...)` text, grouped with its owning action in the trajectory and retained in `console.txt`. The converter preserves full code and output without truncation; large traces may take longer to load. Older sessions without display records still require `console.txt` for that output. Missing usage remains unknown rather
than zero. These files are included in the job archive.

The timeline groups each model action with its display output and nested cell results. Request/cell timing, cell status, full timestamps and source snapshots remain in extension data, accessible in the trajectory JSON from Harbor’s Agent files tab; structured details and every parsed native record are retained in ATIF extension fields, including provider metadata and unknown event types. Missing or invalid timestamps remain unknown. Unreadable log fragments are retained as base64 with their line numbers. This preserves recorded evidence, not unrecorded external effects.

## Further reading

- [Project design](docs/design.md): the relationship between REPL, agent and launcher.
- [Agent design](docs/agent/README.md): the work loop, state, recovery and self-modification.
- [Agent maintenance instructions](AGENTS.md): how coding agents implement, verify and maintain this directory using the design.
