#!/usr/bin/env bash
#
# Stop/resume-safe full sweep: runs FORMATS x MODELS over all 89 tasks, but
# every cell auto-resumes. For each (model, format):
#   * if a prior run dir exists -> --cont (skips tasks that already have a
#     .aider.results.json, including error results; re-runs only the missing)
#   * else -> --new
# So you can Ctrl-C / kill at any point and just re-run this script: it picks up
# exactly where it left off on every cell. Prints a per-model comparison table.
#
# Usage:
#   ./resume_full_sweep.sh                 # foreground, tees to tmp.benchmarks/full_run.log
#   nohup ./resume_full_sweep.sh &         # detached; tail -f tmp.benchmarks/full_run.log
#
# Env knobs:
#   MODELS   "glm-5.1 gpt-5.5 deepseek-v3.2 claude-opus-4-6-thinking"
#   FORMATS  "diff udiff whole"
#   THREADS  16
#   TRIES    1
#
# Notes:
#   * Thinking + per-request timeout (300s) come from .aider.model.settings.yml.
#   * If a cell has several leftover dirs, the one with the most completed
#     results is kept (restored into tmp.benchmarks/) and the rest are moved to
#     tmp.benchmarks/OLD/ so --cont is unambiguous and summaries don't double-count.
set -uo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source .benchmark.env

MODELS="${MODELS:-glm-5.1 gpt-5.5 deepseek-v3.2 claude-opus-4-6-thinking}"
FORMATS="${FORMATS:-diff udiff whole}"
THREADS="${THREADS:-16}"
TRIES="${TRIES:-1}"
LOG="tmp.benchmarks/full_run.log"
mkdir -p tmp.benchmarks tmp.benchmarks/OLD

# Consolidate any leftover dirs for a run name and decide --cont vs --new.
# Sets globals: CELL_FLAG ("--cont"|"--new"), CELL_DONE (count in the kept dir).
prep_cell() {
  local run_name="$1" best="" bestn=-1 d n base
  shopt -s nullglob
  for d in tmp.benchmarks/*--"$run_name" tmp.benchmarks/OLD/*--"$run_name"; do
    [[ -d "$d" ]] || continue
    n=$(find "$d" -name '.aider.results.json' 2>/dev/null | wc -l)
    if (( n > bestn )); then bestn="$n"; best="$d"; fi
  done
  CELL_FLAG="--new"; CELL_DONE=""
  if [[ -n "$best" ]]; then
    base="$(basename "$best")"
    # move every matching dir in tmp.benchmarks/ aside, then restore the best one
    for d in tmp.benchmarks/*--"$run_name"; do [[ -d "$d" ]] && mv "$d" tmp.benchmarks/OLD/ 2>/dev/null; done
    if [[ -d "tmp.benchmarks/OLD/$base" ]]; then mv "tmp.benchmarks/OLD/$base" tmp.benchmarks/ 2>/dev/null; fi
    CELL_FLAG="--cont"; CELL_DONE="$bestn"
  fi
  shopt -u nullglob
}

{
  echo "=== resumable sweep started: $(date) ===  MODELS=[$MODELS] FORMATS=[$FORMATS] THREADS=$THREADS TRIES=$TRIES"

  for m in $MODELS; do
    case "$m" in */*) litellm_model="$m" ;; *) litellm_model="openai/$m" ;; esac
    for f in $FORMATS; do
      run_name="full-$m-$f"
      prep_cell "$run_name"
      echo ""
      echo "######## MODEL: $m / $f  ($CELL_FLAG${CELL_DONE:+, $CELL_DONE/89 already done}) ########"
      .venv/bin/python -u benchmark/benchmark.py "$run_name" \
        --model "$litellm_model" --edit-format "$f" --threads "$THREADS" \
        --exercises-dir refactor-benchmark "$CELL_FLAG" --tries "$TRIES" \
        || echo "WARN: $m/$f exited non-zero"
    done

    echo ""
    echo "==================== SUMMARY: $m ===================="
    sdirs=(tmp.benchmarks/*--"full-$m"-*)
    if [[ -e "${sdirs[0]}" ]]; then .venv/bin/python summarize_runs.py "${sdirs[@]}" || true; fi
  done

  echo ""
  echo "ALL_DONE: $(date)"
} 2>&1 | tee "$LOG"
