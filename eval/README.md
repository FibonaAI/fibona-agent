# Fibona eval

Evaluates Fibona on Harbor datasets and archives every run. One config is one job, which produces
`experiments/<run>/`, where `<run>` is `<UTC time>-<name>`.

```bash
mkdir -p ~/fibona-eval-configs
cp eval/example.yaml ~/fibona-eval-configs/my-job.yaml   # Configs live outside the repo.
make -C eval job CONFIG=~/fibona-eval-configs/my-job.yaml
```

A job records the code (commit and uncommitted patch) → mirrors task images → launches ECS workers → syncs Fibona
→ runs Harbor → deletes the workers → uploads to TOS.

## Prerequisites

- In the environment: `VOLCENGINE_ACCESS_KEY`/`VOLCENGINE_SECRET_KEY`, `TOS_ACCESS_KEY`/`TOS_SECRET_KEY`,
  `OPENAI_API_KEY` (optionally `OPENAI_BASE_URL`).
- Read access to the SSH key in `settings.pk`; git `user.name` and `user.email` set.
- Run from an amd64 Linux controller with `uv`, Git, Make, SSH, rsync, curl and tar. Workers need
  Docker/Compose, Python 3, GNU `timeout`, the configured SSH login and a writable `/opt/fibona`.
  Replace the example hosts and cloud resource IDs with resources available to your account.
- Export credentials before launching; `make job` does not load `.env` or `.zshenv` itself.

## Config

See `example.yaml` for a full example; unknown keys are rejected.

| Key | Meaning |
|---|---|
| `name` | Run name suffix. |
| `archive` | TOS archive location, `tos://bucket/prefix`. |
| `dataset` / `model` | As `harbor run -d` / `-m` take them. |
| `base_url` | Optional model API endpoint. |
| `agent` | Optional Harbor agent name or importable `module:Class`; defaults to `plugins.agent:Fibona`. |
| `model_settings` | Optional: `reasoning_effort`, `max_output_tokens`, `api_retries`, `context_tokens`, `extra_body` (JSON request extensions). |
| `settings` | SSH user, key, default `port` and per-host `ports`; `registry` is where task images are mirrored. |
| `groups` | Where tasks run: either `hosts` (fixed hosts with resource budgets) or `ecs` (workers launched for the job). `tasks` globs match in order; empty is the catch-all. |
| `exclude` / `limit` | Tasks to skip / at most this many tasks. |
| `extra_args` | Passed to `harbor run` as is. |

For adapters using the shared environment helper, explicit YAML values override environment defaults.
`model_settings.xxx` maps to `FIBONA_XXX` (objects use JSON), `model` to `FIBONA_MODEL`, and
`base_url` to `OPENAI_BASE_URL`; supported settings depend on the adapter.

Eligible failed tasks are retried up to twice by default; zero scores do not trigger retries,
and earlier attempts' sessions are not retained.

## Make targets

- `make job CONFIG=...`: run one job; the only entry point.
- `make dist`, `make bin/crane`: run by `job`; rarely needed on their own.
- `make image HOST=... KEY=... PUBKEY=...`: build a new CPU worker image.

## Results and stopping

- Start with `meta.json` (run info, git, exit code) and `jobs/<run>/result.json` (Harbor results); per-trial logs
  are under `jobs/<run>/`.
- Collected files are archived to `<archive>/<run>/` in TOS after credential redaction.
- Ctrl-C or `kill` triggers worker cleanup and archiving; fixed hosts are not deleted. After a crash or
  cleanup failure, check `meta.json` for `release_error` and instances tagged `fibona-job=<run>` in ECS.

## Notes for agents

- Run evaluations only through `make job`; add new behavior as a module under `steps/`.
- A job costs money: get the user's go-ahead first, and use a small `limit` when checking a change.
- Never put credentials in configs or the repo.
