#!/usr/bin/env bash
#
# Purge Apollo OPERATIONAL data in DEV.
#
# Truncates the alert/case/dedup/triage/investigation/response operational
# tables in the DEV Apollo database and KEEPS customers, api_keys, users and
# config. This is the "clear the seeded/synthetic alerts" operation.
#
# It does NOT touch code or infrastructure. It dispatches the existing
# GitHub Actions workflow (apollo/.github/workflows/purge-data.yml) which
# runs a one-off Fargate task against the env's own DB via OIDC. Everything
# goes through the workflow on purpose (no direct AWS credentials needed here,
# only `gh` auth).
#
# SAFETY: dry-run by default. It only deletes when you pass --live.
#
# Usage:
#   ./purge-dev.sh              # DRY RUN: prints what would be purged, deletes nothing
#   ./purge-dev.sh --live       # LIVE: actually truncates operational tables in dev
#   ./purge-dev.sh --live --flush   # LIVE and attempt the real dedup flush (dev engine is healthy)
#
# Requirements: gh (authenticated), the purge-data.yml workflow present on main.
#
# See ../README.md for the full story, the exit-code map, and why we skip the
# dedup flush by default.

set -euo pipefail

REPO="tekstream-cip/apollo"
WORKFLOW="purge-data.yml"
REF="main"
ENVIRONMENT="dev"          # this script is LOCKED to dev on purpose
SCOPE="operational"        # never 'full' here (full wipes customers/api_keys/users)

LIVE=0
SKIP_DEDUP="true"          # skip the dedup flush by default (self-heals via 30-min TTL)
for arg in "$@"; do
  case "$arg" in
    --live)  LIVE=1 ;;
    --flush) SKIP_DEDUP="false" ;;   # opt back into the real per-tenant dedup flush
    -h|--help)
      sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done

command -v gh >/dev/null || { echo "ERROR: gh CLI not found." >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "ERROR: gh not authenticated. Run: gh auth login" >&2; exit 1; }

if [ "$LIVE" = "1" ]; then
  DRY_RUN="false"
  CONFIRM="PURGE ${ENVIRONMENT}"     # the workflow requires this exact string on a live run
  echo "*** LIVE purge of ${ENVIRONMENT} OPERATIONAL data. This DELETES rows. ***"
else
  DRY_RUN="true"
  CONFIRM=""                          # ignored in dry-run
  echo "Dry run against ${ENVIRONMENT} (no deletion). Pass --live to actually purge."
fi

echo "  scope=${SCOPE}  skip_dedup_flush=${SKIP_DEDUP}  ref=${REF}"

# Dispatch, with a small retry for the transient GitHub 5xx we have hit before.
dispatch() {
  gh workflow run "$WORKFLOW" -R "$REPO" --ref "$REF" \
    -f environment="$ENVIRONMENT" \
    -f dry_run="$DRY_RUN" \
    -f scope="$SCOPE" \
    -f skip_dedup_flush="$SKIP_DEDUP" \
    -f acknowledge_no_snapshot="true" \
    -f confirm="$CONFIRM"
}
n=0
until dispatch 2>/tmp/purge-dispatch.err; do
  n=$((n+1)); cat /tmp/purge-dispatch.err >&2
  [ "$n" -ge 4 ] && { echo "ERROR: dispatch failed after $n attempts." >&2; exit 1; }
  echo "dispatch failed (likely transient GitHub 5xx); retry $n in 6s..." >&2; sleep 6
done

sleep 8
RUN_ID=$(gh run list -R "$REPO" --workflow="$WORKFLOW" --limit 1 --json databaseId --jq '.[0].databaseId')
echo "Run: https://github.com/${REPO}/actions/runs/${RUN_ID}"
gh run watch "$RUN_ID" -R "$REPO" --exit-status --interval 10 || true

CONCLUSION=$(gh run view "$RUN_ID" -R "$REPO" --json conclusion --jq '.conclusion')
echo "Conclusion: ${CONCLUSION}"
if [ "$CONCLUSION" != "success" ]; then
  echo "Purge did NOT succeed. Read the exit-code map in ../README.md (esp. 10-30 = flush aborted BEFORE truncate, so no rows were deleted)." >&2
  exit 1
fi
echo "OK: ${ENVIRONMENT} operational purge finished (${DRY_RUN:+dry_run=$DRY_RUN})."
