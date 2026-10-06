#!/usr/bin/env bash
# Run the SOAR push-gate test against Apollo DEV as the FTCC tenant.
# Reuses the harness in ../prod (env-agnostic; only URL/key/customer differ).
#
#   ./run.sh                 # single push -> expect "new"
#   ./run.sh --replay        # +replay same SDI -> expect "duplicate"
#   ./run.sh --replay --content-replay   # full 3-pass e2e
#
# Reads dev/.env (this folder). Apollo-only by default (--no-crosscheck); drop
# that flag once GATEWAY_URL + CIP_API_KEY are set in .env for the CIP cross-check.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
set -a; source "$HERE/.env"; set +a

if [ -z "${APOLLO_API_KEY:-}" ]; then
  echo "ERROR: APOLLO_API_KEY is empty in dev/.env — mint the dev FTCC key first (see README)." >&2
  exit 1
fi

CROSS="--no-crosscheck"
if [ -n "${GATEWAY_URL:-}" ] && [ -n "${CIP_API_KEY:-}" ]; then CROSS=""; fi

exec python3 "$HERE/../prod/soar_gate_test.py" \
  --url "$APOLLO_URL" \
  --api-key "$APOLLO_API_KEY" \
  --customer-id "${APOLLO_CUSTOMER_ID:-}" \
  ${CROSS} "$@"
