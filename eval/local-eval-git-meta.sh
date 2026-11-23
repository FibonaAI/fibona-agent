#!/usr/bin/env bash

# Capture repository provenance for the direct Harbor entrypoints. The Python
# job entrypoint has the same responsibility through steps.code.record().
local_eval_prepare() {
  local jobs_dir=$1
  LOCAL_EVAL_META_TMP=$(mktemp -d "${TMPDIR:-/tmp}/fibona-local-eval.XXXXXX")
  LOCAL_EVAL_START_MARKER="$LOCAL_EVAL_META_TMP/start"
  : > "$LOCAL_EVAL_START_MARKER"
  LOCAL_EVAL_COMMIT=$(git rev-parse HEAD)
  LOCAL_EVAL_BRANCH=$(git rev-parse --abbrev-ref HEAD)
  LOCAL_EVAL_STATUS=$(git status --short)
  git diff --binary HEAD > "$LOCAL_EVAL_META_TMP/git.patch"
  export LOCAL_EVAL_META_TMP LOCAL_EVAL_START_MARKER LOCAL_EVAL_COMMIT LOCAL_EVAL_BRANCH LOCAL_EVAL_STATUS
}

local_eval_finish() {
  local jobs_dir=$1
  local exit_code=$2
  local run_dir
  run_dir=$(find "$jobs_dir" -mindepth 1 -maxdepth 1 -type d -newer "$LOCAL_EVAL_START_MARKER" -print 2>/dev/null | sort | tail -n 1)
  if [[ -z "$run_dir" ]]; then
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
