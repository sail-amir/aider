#!/usr/bin/env bash
#
# Run aider's refactor benchmark against the local/proxy endpoint.
#
# Usage:
#   ./run_refactor_bench.sh <model> <run-name> [extra benchmark.py args...]
#
# Examples:
#   # one tiny task, single try (quick smoke test):
#   ./run_refactor_bench.sh deepseek-v3.2 smoke --keywords baseconv --tries 1 --new
#
#   # full 89-task run, 5 parallel threads:
#   ./run_refactor_bench.sh deepseek-v3.2 ds-full --threads 5 --new
#
#   # show stats for an existing run dir (no model calls):
#   ./run_refactor_bench.sh deepseek-v3.2 ignore --stats-only --cont
#
# Notes:
#   * <model> is the served id from the gateway (glm-5.1, gpt-5.5, deepseek-v3.2,
#     claude-opus-4-8-aws, ...). It is automatically prefixed with "openai/" so
#     litellm routes it to the OpenAI-compatible endpoint in .benchmark.env.
#   * Edit format defaults to "whole" (the laziness-sensitive format). Override
#     by passing e.g. --edit-format diff.
#   * The refactor _test.py only does ast.parse (no execution of model code),
#     so Docker is NOT required for safety here.
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .benchmark.env ]]; then
  echo "ERROR: .benchmark.env not found (endpoint creds). See setup notes." >&2
  exit 1
fi
# shellcheck disable=SC1091
source .benchmark.env

MODEL="${1:?usage: $0 <model> <run-name> [extra args]}"
RUN_NAME="${2:?usage: $0 <model> <run-name> [extra args]}"
shift 2

# Prefix with openai/ unless caller already qualified it.
case "$MODEL" in
  */*) LITELLM_MODEL="$MODEL" ;;
  *)   LITELLM_MODEL="openai/$MODEL" ;;
esac

# Edit format: override with EDIT_FORMAT env (diff|udiff|whole|diff-fenced).
# Default "whole" (aider's fallback for unrecognized models).
EDIT_FORMAT="${EDIT_FORMAT:-whole}"

# Thinking/reasoning effort. Default comes from .aider.model.settings.yml
# (reasoning_effort: high for glm-5.1/gpt-5.5/deepseek-v3.2). Override per run:
#   REASONING_EFFORT=off      -> non-thinking baseline
#   REASONING_EFFORT=low|medium|high
# Note: has no effect on claude-opus-4-6-thinking (thinking is fixed by alias).
if [[ -n "${REASONING_EFFORT:-}" ]]; then
  export AIDER_REASONING_EFFORT="$REASONING_EFFORT"
fi

set -x
exec .venv/bin/python benchmark/benchmark.py "$RUN_NAME" \
  --model "$LITELLM_MODEL" \
  --edit-format "$EDIT_FORMAT" \
  --threads 1 \
  --exercises-dir refactor-benchmark \
  "$@"
