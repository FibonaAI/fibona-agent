#!/usr/bin/env bash
set -eo pipefail
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
  --include-task-name 'terminal-bench/mvcc-lsm-compaction' \
  --include-task-name 'terminal-bench/retro-console-soc' \
  --include-task-name 'terminal-bench/telecom-entity-resolution' \
  --include-task-name 'terminal-bench/vf2-speedup-networkx' \
  --include-task-name 'terminal-bench/ctr-optimization' \
  --include-task-name 'terminal-bench/distributed-dedup' \
  --include-task-name 'terminal-bench/layout-config-recreation' \
  --include-task-name 'terminal-bench/session-window-debug' \
  --include-task-name 'terminal-bench/freecad-spring-clip' \
  --include-task-name 'terminal-bench/html-js-filter' \
  --include-task-name 'terminal-bench/batched-eval-parity' \
  --include-task-name 'terminal-bench/biped-contact-dynamics' \
  --include-task-name 'terminal-bench/intrastat-meldung' \
  --include-task-name 'terminal-bench/live-database-cutover' \
  --include-task-name 'terminal-bench/vba-userform-port' \
  --agent plugins.agent:Fibona \
  --model gpt-6-astra \
  --ak reasoning_effort=max \
  --agent-setup-timeout-multiplier 6 \
  --env docker \
  --n-attempts 1 \
  --n-concurrent 64 \
  --max-retries 3 \
  --jobs-dir "$PWD/jobs" \
  --delete "$@"
