#!/usr/bin/env bash
#
# Sweep a model across multiple aider edit formats on the refactor benchmark
# and print a comparison table (which formats the model "supports" well).
#
# Usage:
#   ./sweep_edit_formats.sh <model> <run-prefix> [extra benchmark.py args...]
#
# Examples:
#   # quick: 3 small tasks, all 4 formats, 1 try each
#   ./sweep_edit_formats.sh deepseek-v3.2 ds-cmp \
#       --keywords baseconv,output_hash,ogrinspect --tries 1
#
#   # full: all 89 tasks, all 4 formats, 5 threads
#   FORMATS="diff udiff whole diff-fenced" THREADS=5 \
#       ./sweep_edit_formats.sh deepseek-v3.2 ds-full
#
# Env knobs:
#   FORMATS  space-separated list (default: "diff udiff whole diff-fenced")
#   THREADS  parallel threads per format run (default: 1)
#
# Notes:
#   * <model> is the gateway id; "openai/" is prepended automatically.
#   * Each format gets its own dated run dir: tmp.benchmarks/<date>--<prefix>-<fmt>
#   * "well_formed%" is the key signal for format support: it's the share of
#     cases where the model emitted a parseable edit in the requested format.
set -euo pipefail
cd "$(dirname "$0")"
# shellcheck disable=SC1091
source .benchmark.env

MODEL="${1:?usage: $0 <model> <run-prefix> [extra args]}"
PREFIX="${2:?usage: $0 <model> <run-prefix> [extra args]}"
shift 2

case "$MODEL" in
  */*) LITELLM_MODEL="$MODEL" ;;
  *)   LITELLM_MODEL="openai/$MODEL" ;;
esac

FORMATS="${FORMATS:-diff udiff whole diff-fenced}"
THREADS="${THREADS:-1}"

# Thinking/reasoning effort (see run_refactor_bench.sh). Default = high from
# .aider.model.settings.yml. REASONING_EFFORT=off for a non-thinking sweep.
if [[ -n "${REASONING_EFFORT:-}" ]]; then
  export AIDER_REASONING_EFFORT="$REASONING_EFFORT"
fi

run_dirs=()
for fmt in $FORMATS; do
  run_name="${PREFIX}-${fmt}"
  echo ""
  echo "########################################################################"
  echo "# $MODEL  |  edit-format: $fmt  |  run: $run_name"
  echo "########################################################################"
  .venv/bin/python -u benchmark/benchmark.py "$run_name" \
    --model "$LITELLM_MODEL" \
    --edit-format "$fmt" \
    --threads "$THREADS" \
    --exercises-dir refactor-benchmark \
    --new "$@" || echo "WARN: run for $fmt exited non-zero"

  # newest run dir matching this format
  d=$(ls -dt tmp.benchmarks/*--"${run_name}" 2>/dev/null | head -1 || true)
  [[ -n "$d" ]] && run_dirs+=("$d")
done

echo ""
echo "========================== COMPARISON =========================="
.venv/bin/python summarize_runs.py "${run_dirs[@]}"
