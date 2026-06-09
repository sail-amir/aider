#!/usr/bin/env bash
#
# Full refactor-benchmark sweep: run several edit formats across all four
# gateway models, thinking ON, over all 89 tasks, then print a comparison
# table per model.
#
# Usage:
#   ./run_full_sweep.sh                 # foreground, tees to tmp.benchmarks/full_run.log
#   nohup ./run_full_sweep.sh &         # detached; watch: tail -f tmp.benchmarks/full_run.log
#
# Env knobs (with defaults):
#   MODELS    "glm-5.1 gpt-5.5 deepseek-v3.2 claude-opus-4-6-thinking"
#   FORMATS   "diff udiff whole"   (passed through to sweep_edit_formats.sh)
#   THREADS   16                   (parallel requests per format run)
#   TRIES     1                    (1 = pass@1 / leaderboard-style; 2 = aider default)
#   REASONING_EFFORT (unset)       off|low|medium|high — overrides settings.yml default
#
# Notes:
#   * Thinking is ON by default (reasoning_effort: high in .aider.model.settings.yml
#     for glm/gpt/deepseek; via the alias for claude-opus-4-6-thinking).
#   * ALL 89 tasks run. The 252K-token outlier
#     (common_methods_invocations_..._sample_rightmost_arg) exceeds glm (200K)
#     and deepseek (131K) context -> expect it to fail/exhaust for those; it is
#     a slow ~252K-token request for gpt-5.5 / claude (1M context).
#   * threads=16 plus any other job on the same gateway can trigger rate limits;
#     if error_outputs/429s climb, lower THREADS and rerun the affected model.
set -euo pipefail
cd "$(dirname "$0")"

MODELS="${MODELS:-glm-5.1 gpt-5.5 deepseek-v3.2 claude-opus-4-6-thinking}"
export FORMATS="${FORMATS:-diff udiff whole}"
export THREADS="${THREADS:-16}"
TRIES="${TRIES:-1}"
LOG="tmp.benchmarks/full_run.log"

mkdir -p tmp.benchmarks
: > "$LOG"

{
  echo "=== full sweep started: $(date) ==="
  echo "MODELS=[$MODELS]  FORMATS=[$FORMATS]  THREADS=$THREADS  TRIES=$TRIES"
  for m in $MODELS; do
    echo ""
    echo "######################## MODEL: $m ########################"
    ./sweep_edit_formats.sh "$m" "full-$m" --tries "$TRIES" \
      || echo "WARN: sweep for $m exited non-zero"
  done

  echo ""
  echo "######################## FINAL SUMMARIES ########################"
  for m in $MODELS; do
    echo ""
    dirs=(tmp.benchmarks/*--"full-$m"-*)
    if [[ -e "${dirs[0]}" ]]; then
      .venv/bin/python summarize_runs.py "${dirs[@]}" || true
    fi
  done
  echo ""
  echo "ALL_DONE: $(date)"
} 2>&1 | tee "$LOG"
