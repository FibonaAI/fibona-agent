# Fibona eval

Evaluates Fibona on Harbor datasets and archives every run. One config is one job, which produces
`experiments/<run>/`, where `<run>` is `<UTC time>-<name>`.

```bash
cp eval/example.yaml ~/fibona-eval-configs/my-job.yaml   # Configs live outside the repo.
make -C eval job CONFIG=~/fibona-eval-configs/my-job.yaml
```

A job records the code (commit and uncommitted patch) → mirrors task images → launches ECS workers → syncs Fibona
→ runs Harbor → deletes the workers → uploads to TOS.

## Prerequisites

- In the environment: `VOLCENGINE_ACCESS_KEY`/`VOLCENGINE_SECRET_KEY`, `TOS_ACCESS_KEY`/`TOS_SECRET_KEY`,
  `OPENAI_API_KEY` (optionally `OPENAI_BASE_URL`).
- Read access to the SSH key in `settings.pk`; git `user.name` and `user.email` set.

## Config

See `example.yaml` for a full example; unknown keys are rejected.

| Key | Meaning |
|---|---|
| `name` | Run name suffix. |
| `archive` | TOS archive location, `tos://bucket/prefix`. |
| `dataset` / `model` | As `harbor run -d` / `-m` take them. |
| `model_settings` | Optional: `reasoning_effort`, `max_output_tokens`, `api_retries`. |
| `settings` | SSH user, key and port; `registry` is where task images are mirrored. |
| `groups` | Where tasks run: either `hosts` (fixed hosts with resource budgets) or `ecs` (workers launched for the job). `tasks` globs match in order; empty is the catch-all. |
| `exclude` / `limit` | Tasks to skip / at most this many tasks. |
| `extra_args` | Passed to `harbor run` as is. |

## Make targets

- `make job CONFIG=...`: run one job; the only entry point.
- `make dist`, `make bin/crane`: run by `job`; rarely needed on their own.
- `make image HOST=... KEY=... PUBKEY=...`: build a new CPU worker image.

## Results and stopping

- Start with `meta.json` (run info, git, exit code) and `jobs/<run>/result.json` (Harbor results); per-trial logs
  are under `jobs/<run>/`.
- Ctrl-C or `kill` still deletes the workers and archives the job. Only `SIGKILL` doesn't: then delete the
  instances tagged `fibona-job=<run>` in the ECS console.

## Notes for agents

- Run evaluations only through `make job`; add new behavior as a module under `steps/`.
- A job costs money: get the user's go-ahead first, and use a small `limit` when checking a change.
- Never put credentials in configs or the repo.
