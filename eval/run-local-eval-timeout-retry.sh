#!/usr/bin/env bash
set -eo pipefail
# Retry the 7 API-524 failures from jobs/2026-11-11__00-18-56.
# This reruns Fibona from scratch; lower concurrency reduces simultaneous API load.
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
set -a
source "$HOME/.env"
set +a
uv run --no-project --python 3.12 \
  --with harbor==0.24.0 \
  --with volcengine-python-sdk==5.0.54 \
  --with tos==2.9.3 \
  --with 'httpx[socks]' \
  harbor run \
  --dataset terminal-bench/terminal-bench \
  --include-task-name 'terminal-bench/ks-solver-cpp' \
  --include-task-name 'terminal-bench/retro-console-soc' \
  --include-task-name 'terminal-bench/distributed-dedup' \
  --include-task-name 'terminal-bench/freecad-spring-clip' \
  --include-task-name 'terminal-bench/html-js-filter' \
  --include-task-name 'terminal-bench/biped-contact-dynamics' \
  --include-task-name 'terminal-bench/vba-userform-port' \
  --agent plugins.agent:Fibona \
  --model gpt-6-astra \
  --ak reasoning_effort=max \
  --agent-setup-timeout-multiplier 6 \
  --env docker \
  --n-attempts 1 \
  --n-concurrent "${N_CONCURRENT:-64}" \
  --max-retries 3 \
  --jobs-dir "$PWD/jobs" \
  --delete "$@"
