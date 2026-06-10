#!/usr/bin/env bash
#
# Call-point for pangu inference. benchmark.py invokes this per task as:
#   run_pangu_distill.sh <input.jsonl> <output.jsonl>
# It runs the local distill_pangu.py against the pangu endpoint. ALL pangu
# inference params live here / in .pangu.env, so you can tune them later
# without touching benchmark.py.
#
# Required (set in .pangu.env): PANGU_API_URLS, PANGU_CSB_TOKEN
# Optional: PANGU_AUTH_TOKEN PANGU_MODEL PANGU_MAX_TOKENS PANGU_TIMEOUT
#           PANGU_BATCH_SIZE PANGU_MAX_RETRIES PANGU_TEMPERATURE PANGU_TOP_P
#           PANGU_TOP_K PANGU_STREAM
set -uo pipefail
cd "$(dirname "$0")"

IN="${1:?usage: $0 <input.jsonl> <output.jsonl>}"
OUT="${2:?usage: $0 <input.jsonl> <output.jsonl>}"

if [[ -f .pangu.env ]]; then
  # shellcheck disable=SC1091
  source .pangu.env
fi

: "${PANGU_API_URLS:?set PANGU_API_URLS (one or more /v1/chat/completions URLs) in .pangu.env}"
: "${PANGU_CSB_TOKEN:?set PANGU_CSB_TOKEN in .pangu.env}"

# NOTE: $PANGU_API_URLS is intentionally unquoted so multiple space-separated
# URLs are passed as separate args to --api-urls (load balancing).
exec .venv/bin/python distill_pangu.py \
  --input-file "$IN" \
  --output-file "$OUT" \
  --api-urls $PANGU_API_URLS \
  --csb-token "$PANGU_CSB_TOKEN" \
  --auth-token "${PANGU_AUTH_TOKEN:-nokey}" \
  --model "${PANGU_MODEL:-pangu_auto}" \
  --max-tokens "${PANGU_MAX_TOKENS:-120000}" \
  --timeout "${PANGU_TIMEOUT:-3600}" \
  --batch-size "${PANGU_BATCH_SIZE:-1}" \
  --max-retries "${PANGU_MAX_RETRIES:-3}" \
  --temperature "${PANGU_TEMPERATURE:-1.0}" \
  --top-p "${PANGU_TOP_P:-0.8}" \
  --top-k "${PANGU_TOP_K:--1}" \
  ${PANGU_STREAM:+--stream}
