#!/usr/bin/env bash

# Capture repository provenance for the direct Harbor entrypoints. The Python
# job entrypoint has the same responsibility through steps.code.record().
local_eval_configure() {
  REASONING_EFFORT=max
  TAG=
  HARBOR_ARGS=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tag|--reasoning-effort|--jobs-dir|-o)
        if [[ $# -lt 2 || -z $2 ]]; then
          echo "missing value for $1" >&2
          return 2
        fi
        case "$1" in
          --tag) TAG=$2 ;;
          --reasoning-effort) REASONING_EFFORT=$2 ;;
          --jobs-dir|-o) JOBS_DIR=$2 ;;
        esac
        shift 2
        ;;
      --ak|--agent-kwarg)
        [[ $# -ge 2 ]] || return 2
        if [[ $2 == reasoning_effort=* ]]; then
          REASONING_EFFORT=${2#reasoning_effort=}
        else
          HARBOR_ARGS+=("$1" "$2")
        fi
        shift 2
        ;;
      --job-name|--job-name=*)
        echo "job names are generated automatically; use --tag instead" >&2
        return 2
        ;;
      --tag=*) TAG=${1#*=}; shift ;;
      --reasoning-effort=*) REASONING_EFFORT=${1#*=}; shift ;;
      --jobs-dir=*) JOBS_DIR=${1#*=}; shift ;;
      --ak=reasoning_effort=*|--agent-kwarg=reasoning_effort=*)
        REASONING_EFFORT=${1#*reasoning_effort=}
        shift
        ;;
      *) HARBOR_ARGS+=("$1"); shift ;;
    esac
  done
  [[ -n $REASONING_EFFORT && -n $JOBS_DIR ]] || return 2
  LOCAL_EVAL_JOB_NAME=$(python3 - "$AGENT" "$MODEL" "$REASONING_EFFORT" "$TAG" <<'PY'
import re
import sys
from datetime import datetime

parts = [datetime.now().strftime("%Y-%m-%d__%H-%M-%S-%f")]
for value in sys.argv[1:]:
    if value:
        part = re.sub(r"[^\w.-]+", "-", value).strip(".-_")
        if not part:
            raise SystemExit("directory name component must contain letters or numbers")
        parts.append(part)
name = "-".join(parts)
if len(name.encode()) > 240:
    raise SystemExit("experiment name is too long; shorten the model name or tag")
print(name)
PY
  ) || return
  printf 'experiment directory: %s/%s\n' "$JOBS_DIR" "$LOCAL_EVAL_JOB_NAME"
}

local_eval_prepare() {
  local jobs_dir=$1
  LOCAL_EVAL_META_TMP=$(mktemp -d "${TMPDIR:-/tmp}/fibona-local-eval.XXXXXX")
  LOCAL_EVAL_COMMIT=$(git rev-parse HEAD)
  LOCAL_EVAL_BRANCH=$(git rev-parse --abbrev-ref HEAD)
  LOCAL_EVAL_STATUS=$(git status --short)
  git diff --binary HEAD > "$LOCAL_EVAL_META_TMP/git.patch"
  export LOCAL_EVAL_META_TMP LOCAL_EVAL_COMMIT LOCAL_EVAL_BRANCH LOCAL_EVAL_STATUS
}

local_eval_finish() {
  local jobs_dir=$1
  local exit_code=$2
  local run_dir
  run_dir="$jobs_dir/$LOCAL_EVAL_JOB_NAME"
  if [[ ! -d "$run_dir" ]]; then
    echo "warning: could not locate Harbor run directory under $jobs_dir" >&2
    rm -rf "$LOCAL_EVAL_META_TMP"
    return 0
  fi

  cp "$LOCAL_EVAL_META_TMP/git.patch" "$run_dir/git.patch"
  LOCAL_EVAL_EXIT_CODE="$exit_code" python3 - "$run_dir/meta.json" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

meta = {
    "git": {
        "commit": os.environ["LOCAL_EVAL_COMMIT"],
        "branch": os.environ["LOCAL_EVAL_BRANCH"],
        "dirty": bool(os.environ["LOCAL_EVAL_STATUS"]),
        "status": os.environ["LOCAL_EVAL_STATUS"].splitlines(),
    },
    "finished_at": datetime.now(timezone.utc).isoformat(),
    "exit_code": int(os.environ["LOCAL_EVAL_EXIT_CODE"]),
    "entrypoint": "run-local-eval*.sh",
}
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(meta, f, indent=2)
    f.write("\n")
PY
  echo "recorded git commit $LOCAL_EVAL_COMMIT in $run_dir/meta.json"
  rm -rf "$LOCAL_EVAL_META_TMP"
}
