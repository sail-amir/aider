#!/usr/bin/env bash
#
# Resume the interrupted full sweep WITHOUT redoing finished work:
#   * glm-5.1 / diff  -> --cont the prior 87/89 run (re-runs only the 2 that hung)
#   * everything else -> fresh (those cells never produced a run dir)
# Then print a per-model comparison table.
#
# Usage:
#   ./resume_full_sweep.sh                # foreground, tees to tmp.benchmarks/full_run.log
#   nohup ./resume_full_sweep.sh &        # detached; tail -f tmp.benchmarks/full_run.log
#
# Env knobs:
#   THREADS  16     parallel requests per cell
#   TRIES    1      pass@1 / leaderboard-style
#
# Notes:
#   * Relies on benchmark.py's LONG_TIMEOUT (now 90s) so the 2 hung glm/diff
#     tasks fail-fast and get recorded as errors instead of retrying for hours.
#   * The most-complete prior glm-5.1/diff run is auto-detected (most results),
#     restored into tmp.benchmarks/ for --cont; any lesser partial is set aside
#     under tmp.benchmarks/OLD/.
set -uo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source .benchmark.env

THREADS="${THREADS:-16}"
TRIES="${TRIES:-1}"
LOG="tmp.benchmarks/full_run.log"
mkdir -p tmp.benchmarks tmp.benchmarks/OLD

{
  echo "=== resume sweep started: $(date) ===  THREADS=$THREADS TRIES=$TRIES"

  # ---- restore the most-complete glm-5.1/diff run so --cont can resume it ----
  for d in tmp.benchmarks/*--full-glm-5.1-diff; do
    if [[ -d "$d" ]]; then mv "$d" tmp.benchmarks/OLD/ && echo "set aside: $(basename "$d")"; fi
  done
  best=""; bestn=-1
  for d in tmp.benchmarks/OLD/*--full-glm-5.1-diff; do
    if [[ -d "$d" ]]; then
      n=$(find "$d" -name '.aider.results.json' 2>/dev/null | wc -l)
      if (( n > bestn )); then bestn=$n; best="$d"; fi
    fi
  done
  cont_flag="--new"
  if [[ -n "$best" ]]; then
    mv "$best" tmp.benchmarks/ && cont_flag="--cont"
    echo "restored for --cont: $(basename "$best")  ($bestn/89 done)"
  else
    echo "WARN: no prior glm-5.1/diff run found -> starting it fresh"
  fi

  # ---- 1. resume glm-5.1 / diff (reuses done tasks, reruns only the missing) ----
  echo ""
  echo "######## glm-5.1 / diff ($cont_flag) ########"
  .venv/bin/python -u benchmark/benchmark.py full-glm-5.1-diff \
    --model openai/glm-5.1 --edit-format diff --threads "$THREADS" \
    --exercises-dir refactor-benchmark "$cont_flag" --tries "$TRIES" \
    || echo "WARN: glm-5.1/diff exited non-zero"

  # ---- 2. glm-5.1 remaining formats (fresh) ----
  echo ""
  echo "######## glm-5.1 / udiff,whole (fresh) ########"
  FORMATS="udiff whole" THREADS="$THREADS" ./sweep_edit_formats.sh glm-5.1 full-glm-5.1 --tries "$TRIES" \
    || echo "WARN: glm-5.1 udiff/whole exited non-zero"

  # ---- 3. other models, all formats (fresh) ----
  for m in gpt-5.5 deepseek-v3.2 claude-opus-4-6-thinking; do
    echo ""
    echo "######## MODEL: $m / diff,udiff,whole (fresh) ########"
    FORMATS="diff udiff whole" THREADS="$THREADS" ./sweep_edit_formats.sh "$m" "full-$m" --tries "$TRIES" \
      || echo "WARN: sweep for $m exited non-zero"
  done

  # ---- final per-model summaries ----
  echo ""
  echo "######## FINAL SUMMARIES ########"
  for m in glm-5.1 gpt-5.5 deepseek-v3.2 claude-opus-4-6-thinking; do
    echo ""
    dirs=(tmp.benchmarks/*--"full-$m"-*)
    if [[ -e "${dirs[0]}" ]]; then .venv/bin/python summarize_runs.py "${dirs[@]}" || true; fi
  done
  echo ""
  echo "ALL_DONE: $(date)"
} 2>&1 | tee "$LOG"
