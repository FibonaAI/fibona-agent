#!/usr/bin/env bash
set -eo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
set -a
source "$HOME/.env"
set +a
# Use the local Codex subscription login, not API-key authentication.
unset OPENAI_API_KEY OPENAI_BASE_URL CODEX_AUTH_JSON_PATH
export CODEX_FORCE_AUTH_JSON=1
if [[ ! -s "$HOME/.codex/auth.json" ]]; then
  echo "Codex login credentials missing; run codex login first." >&2
  exit 1
fi
uv run --no-project --python 3.12 \
  --with harbor==0.24.0 \
  --with volcengine-python-sdk==5.0.54 \
  --with tos==2.9.3 \
  --with 'httpx[socks]' \
  harbor run \
  --dataset terminal-bench/terminal-bench \
  --include-task-name 'terminal-bench/cad-model' \
  --include-task-name 'terminal-bench/coq-block-bound' \
  --include-task-name 'terminal-bench/cumulative-layout-shift' \
  --include-task-name 'terminal-bench/embedding-drift-monitor' \
  --include-task-name 'terminal-bench/fin-saccr-rwa' \
  --include-task-name 'terminal-bench/formal-crypto' \
  --include-task-name 'terminal-bench/freecad-platform-drawing' \
  --include-task-name 'terminal-bench/heat-pump-warranty' \
  --include-task-name 'terminal-bench/hof-topology-interpenetration' \
  --include-task-name 'terminal-bench/interleaved-vigenere' \
  --include-task-name 'terminal-bench/kv-live-surgery' \
  --include-task-name 'terminal-bench/layout-config-recreation2' \
  --include-task-name 'terminal-bench/legacy-utility-triage' \
  --include-task-name 'terminal-bench/mp-checkpoint-consolidation' \
  --include-task-name 'terminal-bench/nextjs-performance' \
  --include-task-name 'terminal-bench/payments-pipeline-fix' \
  --include-task-name 'terminal-bench/photonic-waveguide-routing' \
  --include-task-name 'terminal-bench/pretrain-shard-corruption' \
  --include-task-name 'terminal-bench/risk-scorer-replay' \
  --include-task-name 'terminal-bench/rs-archive-clone' \
  --include-task-name 'terminal-bench/satb-audio-transcription' \
  --include-task-name 'terminal-bench/shadow-relay' \
  --include-task-name 'terminal-bench/sound-change-cascade' \
  --include-task-name 'terminal-bench/takens-embedding-lean' \
  --include-task-name 'terminal-bench/uefi-bootkit' \
  --include-task-name 'terminal-bench/vpp-loss-divergence' \
  --include-task-name 'terminal-bench/wal-recovery-ordering' \
  --include-task-name 'terminal-bench/wdm-design' \
  --agent plugins.codex:Codex \
  --model "${CODEX_MODEL:-gpt-6.1-terra}" \
  --ak "reasoning_effort=${CODEX_REASONING_EFFORT:-max}" \
  --agent-setup-timeout-multiplier 6 \
  --env docker \
  --n-attempts 1 \
  --n-concurrent 64 \
  --max-retries 3 \
  --jobs-dir "$PWD/jobs" \
  --delete "$@"
