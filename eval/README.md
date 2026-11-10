# Fibona eval

Evaluates Fibona on Harbor datasets and archives every run. One config is one job, which produces
`experiments/<run>/`, where `<run>` is `<UTC time>-<name>`.

```bash
cp eval/cluster.beijing.example.yaml ~/fibona-eval-configs/my-job.yaml   # Or local.example.yaml; configs live outside the repo.
make -C eval job CONFIG=~/fibona-eval-configs/my-job.yaml
```

A job records the code (commit and uncommitted patch) → mirrors task images → launches ECS workers → syncs Fibona
→ runs Harbor → deletes the workers → uploads to TOS.

## Prerequisites

- In the environment: `VOLCENGINE_ACCESS_KEY`/`VOLCENGINE_SECRET_KEY`, `TOS_ACCESS_KEY`/`TOS_SECRET_KEY`,
  `FIBONA_API_KEY` (optionally `FIBONA_BASE_URL`).
- Read access to the SSH key in `settings.pk`; git `user.name` and `user.email` set.

## Config

See `local.example.yaml` (one machine), `cluster.beijing.example.yaml` (ECS workers or fixed hosts) and `cluster.johor.example.yaml` (ECS workers in ap-southeast-1); unknown keys are rejected.

| Key | Meaning |
|---|---|
| `name` | Run name suffix. |
| `archive` | TOS archive location, `tos://bucket/prefix`. |
| `dataset` / `model` | As `harbor run -d` / `-m` take them. |
| `base_url` | Optional model API endpoint. |
| `agent` | Optional Harbor agent name or importable `module:Class`; defaults to `plugins.agent:Fibona`. |
| `model_settings` | Optional: `reasoning_effort`, `max_output_tokens`, `api_retries`, `context_tokens`, `extra_body` (JSON request extensions). |
| `registry` | Optional: task images are mirrored here before the run and pulled from here, local or cluster; every machine's Docker must be logged in to it. Without it, tasks pull from upstream. |
| `region` | Optional, default `cn-beijing`: where ECS workers and vePFS live. Elsewhere workers are reached over their EIPs and `registry` is not allowed. |
| `vepfs` | Optional, cluster jobs: a vePFS mount service; ECS workers mount it at `/vepfs` and its MLP home directory at `/home/users`. |
| `local` | Single machine: run on this machine alone, in its Docker as the current user, within this budget (`cpus`, `memory_mb`, `gpus`). No SSH; replaces `settings` and `groups`. |
| `settings` | Cluster jobs: SSH user, key and port. |
| `groups` | Cluster jobs: where tasks run, either `hosts` (fixed hosts with resource budgets) or `ecs` (workers launched for the job). `tasks` globs match in order; empty is the catch-all. |
| `exclude` / `limit` | Tasks to skip / at most this many tasks. |
| `extra_args` | Passed to `harbor run` as is. |

For adapters using the shared environment helper, explicit YAML values override environment defaults.
`model_settings.xxx` maps to `FIBONA_XXX` (objects use JSON), `model` to `FIBONA_MODEL`, and
`base_url` to `FIBONA_BASE_URL`; supported settings depend on the adapter.

The default agent starts a new `fibona.py` process for each trial attempt, and for each step of a multi-step task;
each process sends its own `prompt_cache_key` (see the [README](../README.md)), so a retried trial gets a new one.
The agent rejects a `prompt_cache_key` in `model_settings.extra_body`.

## Make targets

- `make job CONFIG=...`: run one job; the only entry point.
- `make dist`, `make bin/crane`: run by `job`; rarely needed on their own.
- `make image HOST="<ip> ..." KEY=... PUBKEY=...`: build a new CPU worker image, one host per region.

## Results and stopping

- Start with `meta.json` (run info, git, exit code) and `jobs/<run>/result.json` (Harbor results); per-trial logs
  are under `jobs/<run>/`.
- Ctrl-C or `kill` still deletes the workers and archives the job. Only `SIGKILL` doesn't: then delete the
  instances tagged `fibona-job=<run>` in the ECS console.

## Notes for agents

- Run evaluations only through `make job`; add new behavior as a module under `steps/`.
- A job costs money: get the user's go-ahead first, and use a small `limit` when checking a change.
- Never put credentials in configs or the repo.
