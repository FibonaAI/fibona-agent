# Fibona eval

Evaluates Fibona on Harbor datasets and archives every run. One config is one job, which produces
`experiments/<run>/`, where `<run>` is `<UTC time>-<name>`.

```bash
cp eval/example.yaml ~/fibona-eval-configs/my-job.yaml   # Configs live outside the repo.
make -C eval job CONFIG=~/fibona-eval-configs/my-job.yaml
```

A job records the code (commit and uncommitted patch) → mirrors task images → launches ECS workers
→ runs Harbor → deletes the workers → uploads to TOS.

## Prerequisites

- In the environment: `VOLCENGINE_ACCESS_KEY`/`VOLCENGINE_SECRET_KEY`, `TOS_ACCESS_KEY`/`TOS_SECRET_KEY`,
  `FIBONA_API_KEY` (optionally `FIBONA_BASE_URL`).
- Read access to the SSH key in `settings.pk`; git `user.name` and `user.email` set.

Both Fibona adapters upload the current local source during installation, including uncommitted changes.
Task containers install uv, Python 3.12 and locked dependencies, so they need network access to download them.
Both adapters switch Ubuntu official APT sources to the Huawei Cloud mirror before installing system dependencies, including legacy `.list` and deb822 `.sources` files.
Fedora uses the Aliyun mirror; Fedora 42 and older use its archive, with package signature verification retained.
No prebuilt wheel bundle or shared `/opt/fibona` mount is needed.

## Config

See `example.yaml` for a full example; unknown keys are rejected.

| Key | Meaning |
|---|---|
| `name` | Run name suffix. |
| `archive` | TOS archive location, `tos://bucket/prefix`. |
| `dataset` / `model` | As `harbor run -d` / `-m` take them. |
| `agent` | Optional Harbor agent name or importable `module:Class`; defaults to `plugins.agent:Fibona`. |
| `model_settings` | Optional: `reasoning_effort`, `lowest_reasoning_effort`, `api_retries`, `extra_body` (JSON request extensions). |
| `settings` | SSH user, key and port; `registry` is where task images are mirrored. |
| `groups` | Where tasks run: either `hosts` (fixed hosts with resource budgets) or `ecs` (workers launched for the job). `tasks` globs match in order; empty is the catch-all. |
| `exclude` / `limit` | Tasks to skip / at most this many tasks. |
| `extra_args` | Passed to `harbor run` as is. |

For adapters using the shared environment helper, model settings come only from YAML;
omitted values use code defaults. Only `FIBONA_API_KEY` and `FIBONA_BASE_URL` are read from the launch environment.
`reasoning_effort` and `lowest_reasoning_effort` go directly to Fibona's corresponding CLI options.
Both default to `medium`; lower requests are raised to the minimum. Set both to `low` to use low effort.
`max_output_tokens` and `context_tokens` are no longer accepted in `model_settings`.
Other `model_settings.xxx` values map to `FIBONA_XXX` (objects use JSON), and `model` to `FIBONA_MODEL`;
supported settings depend on the adapter. Set the API endpoint only through `FIBONA_BASE_URL`.
Existing configs with `base_url` must move that value to the environment and remove the YAML field.

## Make targets

- `make job CONFIG=...`: run one job; the only entry point.
- `make bin/crane`: run by `job`; rarely needed on its own.
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
