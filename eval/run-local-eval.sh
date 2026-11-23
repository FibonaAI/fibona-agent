#!/usr/bin/env bash
set -eo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
set -a
source "$HOME/.env"
set +a

usage() {
  echo "用法: $0 <model> [--agent fibona|codex] [--split all|split-0|split-1|split-2] [--base-url URL] [--api-key KEY] [Harbor 参数...]" >&2
  exit 2
}

[[ $# -ge 1 ]] || usage
MODEL=$1
shift
SPLIT=split-0
AGENT=fibona
while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent)
      [[ $# -ge 2 ]] || usage
      AGENT=$2
      shift 2
      ;;
    --split)
      [[ $# -ge 2 ]] || usage
      SPLIT=$2
      shift 2
      ;;
    --base-url)
      [[ $# -ge 2 ]] || usage
      export FIBONA_BASE_URL=$2
      export OPENAI_BASE_URL=$2
      shift 2
      ;;
    --api-key)
      [[ $# -ge 2 ]] || usage
      export FIBONA_API_KEY=$2
      export OPENAI_API_KEY=$2
      shift 2
      ;;
    --)
      shift
      break
      ;;
    *)
      break
      ;;
  esac
done

case "$AGENT" in
  fibona)
    AGENT_SPEC=plugins.agent:Fibona
    ;;
  codex)
    AGENT_SPEC=plugins.codex:Codex
    # Clear caller-provided OpenAI settings, then apply Fibona's configuration.
    unset OPENAI_API_KEY OPENAI_BASE_URL
    if [[ -n ${FIBONA_API_KEY:-} ]]; then
      export OPENAI_API_KEY=$FIBONA_API_KEY
    fi
    if [[ -n ${FIBONA_BASE_URL:-} ]]; then
      export OPENAI_BASE_URL=$FIBONA_BASE_URL
    fi
    ;;
  *)
    echo "未知 agent: $AGENT（可选 fibona、codex）" >&2
    exit 2
    ;;
esac

TASK_ARGS=()
case "$SPLIT" in
  all)
    # No include filter: Harbor runs every task in the dataset.
    ;;
  split-0)
    TASK_ARGS+=(
      --include-task-name 'terminal-bench/cad-model'
      --include-task-name 'terminal-bench/coq-block-bound'
      --include-task-name 'terminal-bench/cumulative-layout-shift'
      --include-task-name 'terminal-bench/embedding-drift-monitor'
      --include-task-name 'terminal-bench/fin-saccr-rwa'
      --include-task-name 'terminal-bench/formal-crypto'
      --include-task-name 'terminal-bench/freecad-platform-drawing'
      --include-task-name 'terminal-bench/heat-pump-warranty'
      --include-task-name 'terminal-bench/hof-topology-interpenetration'
      --include-task-name 'terminal-bench/interleaved-vigenere'
      --include-task-name 'terminal-bench/kv-live-surgery'
      --include-task-name 'terminal-bench/layout-config-recreation2'
      --include-task-name 'terminal-bench/legacy-utility-triage'
      --include-task-name 'terminal-bench/mp-checkpoint-consolidation'
      --include-task-name 'terminal-bench/nextjs-performance'
      --include-task-name 'terminal-bench/payments-pipeline-fix'
      --include-task-name 'terminal-bench/photonic-waveguide-routing'
      --include-task-name 'terminal-bench/pretrain-shard-corruption'
      --include-task-name 'terminal-bench/risk-scorer-replay'
      --include-task-name 'terminal-bench/rs-archive-clone'
      --include-task-name 'terminal-bench/satb-audio-transcription'
      --include-task-name 'terminal-bench/shadow-relay'
      --include-task-name 'terminal-bench/sound-change-cascade'
      --include-task-name 'terminal-bench/takens-embedding-lean'
      --include-task-name 'terminal-bench/uefi-bootkit'
      --include-task-name 'terminal-bench/vpp-loss-divergence'
      --include-task-name 'terminal-bench/wal-recovery-ordering'
      --include-task-name 'terminal-bench/wdm-design'
    )
    ;;
  split-1)
    TASK_ARGS+=(
      --include-task-name 'terminal-bench/ks-solver-cpp'
      --include-task-name 'terminal-bench/mvcc-lsm-compaction'
      --include-task-name 'terminal-bench/retro-console-soc'
      --include-task-name 'terminal-bench/telecom-entity-resolution'
      --include-task-name 'terminal-bench/vf2-speedup-networkx'
      --include-task-name 'terminal-bench/ctr-optimization'
      --include-task-name 'terminal-bench/distributed-dedup'
      --include-task-name 'terminal-bench/layout-config-recreation'
      --include-task-name 'terminal-bench/session-window-debug'
      --include-task-name 'terminal-bench/freecad-spring-clip'
      --include-task-name 'terminal-bench/html-js-filter'
      --include-task-name 'terminal-bench/batched-eval-parity'
      --include-task-name 'terminal-bench/biped-contact-dynamics'
      --include-task-name 'terminal-bench/intrastat-meldung'
      --include-task-name 'terminal-bench/live-database-cutover'
      --include-task-name 'terminal-bench/vba-userform-port'
    )
    ;;
  split-2)
    TASK_ARGS+=(
      --include-task-name 'terminal-bench/atrx-vep-crispr'
      --include-task-name 'terminal-bench/bun-sourcemap-leak'
      --include-task-name 'terminal-bench/cargo-flight-dispatch'
      --include-task-name 'terminal-bench/data-anonymization'
      --include-task-name 'terminal-bench/foodstuff-beta-activity'
      --include-task-name 'terminal-bench/freecad-impeller'
      --include-task-name 'terminal-bench/freight-dispatch-shift'
      --include-task-name 'terminal-bench/glycan-ms2-elucidation'
      --include-task-name 'terminal-bench/gsea-proteomics'
      --include-task-name 'terminal-bench/lake-temp-glm'
      --include-task-name 'terminal-bench/medical-claims-processing'
      --include-task-name 'terminal-bench/music-harmony'
      --include-task-name 'terminal-bench/ontology-kg-querying'
      --include-task-name 'terminal-bench/production-planning'
      --include-task-name 'terminal-bench/protein-autointerp-disulfide'
      --include-task-name 'terminal-bench/react-lead-form'
      --include-task-name 'terminal-bench/roy-polymath-cn'
      --include-task-name 'terminal-bench/sglang-qwen-burst'
      --include-task-name 'terminal-bench/vllm-deepseek-streaming'
    )
    ;;
  *)
    echo "未知 split: $SPLIT（可选 all、split-0、split-1、split-2）" >&2
    exit 2
    ;;
esac

JOBS_DIR="$PWD/jobs"
printf 'jobs directory: %s\n' "$JOBS_DIR"
source "$PWD/local-eval-git-meta.sh"
local_eval_prepare "$JOBS_DIR"

set +e
uv run --no-project --python 3.12 \
  --with harbor==0.24.0 \
  --with volcengine-python-sdk==5.0.54 \
  --with tos==2.9.3 \
  --with 'httpx[socks]' \
  harbor run \
  --dataset terminal-bench/terminal-bench \
  "${TASK_ARGS[@]}" \
  --agent "$AGENT_SPEC" \
  --model "$MODEL" \
  --ak reasoning_effort=max \
  --agent-setup-timeout-multiplier 6 \
  --env docker \
  --n-attempts 5 \
  --n-concurrent 64 \
  --max-retries 3 \
  --jobs-dir "$JOBS_DIR" \
  --delete "$@"
status=$?
set -e
local_eval_finish "$JOBS_DIR" "$status"
exit "$status"
