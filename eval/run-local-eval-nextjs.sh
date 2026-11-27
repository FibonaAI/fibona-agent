#!/usr/bin/env bash
set -eo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
set -a
source "$HOME/.env"
set +a


usage() {
  echo "用法: $0 <model> [--agent fibona|codex] [--split all|split-0|split-1|split-2] [--base-url URL] [--api-key KEY] [--reasoning-effort max] [--tag TAG] [Harbor 参数...]" >&2
  exit 2
}

[[ $# -ge 1 ]] || usage
MODEL=$1
shift
SPLIT=split-0
AGENT=fibona
EXTRA_ARGS=()
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
      EXTRA_ARGS+=("$1")
      shift
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


JOBS_DIR="$PWD/jobs"
printf 'jobs directory: %s\n' "$JOBS_DIR"
source "$PWD/local-eval-git-meta.sh"
local_eval_configure "${EXTRA_ARGS[@]}" "$@"
local_eval_prepare "$JOBS_DIR"

# Pin Terminal Bench 4.0.0, matching the heat-pump-warranty task version.
set +e
uv run --no-project --python 3.12 \
  --with harbor==0.24.0 \
  --with volcengine-python-sdk==5.0.54 \
  --with tos==2.9.3 \
  --with 'httpx[socks]' \
  harbor run \
  --task 'terminal-bench/nextjs-performance@sha256:81f8315fb984f91208c45b4ac153c8801d07d3beec5eb0f0d3fa9d49b6588fdd' \
  --agent "$AGENT_SPEC" \
  --model "$MODEL" \
  --ak "reasoning_effort=$REASONING_EFFORT" \
  --agent-setup-timeout-multiplier 6 \
  --env docker \
  --n-attempts 20 \
  --n-concurrent 64 \
  --max-retries 3 \
  --jobs-dir "$JOBS_DIR" \
  --job-name "$LOCAL_EVAL_JOB_NAME" \
  --delete "${HARBOR_ARGS[@]}"
status=$?
set -e
local_eval_finish "$JOBS_DIR" "$status"
exit "$status"
