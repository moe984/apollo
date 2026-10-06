#!/usr/bin/env bash
#
# Purge Apollo OPERATIONAL data in PRODUCTION.
#
# Truncates the alert/case/dedup/triage/investigation/response operational
# tables in the PROD Apollo database and KEEPS customers, api_keys (incl. the
# soar_gateway key), users and config. This is the "clear the seeded/synthetic
# alerts" operation for prod.
#
# It does NOT touch code or infrastructure. It dispatches the existing
# GitHub Actions workflow (apollo/.github/workflows/purge-data.yml) which runs
# a one-off Fargate task against prod's own DB via OIDC. Everything goes
# through the workflow on purpose (only `gh` auth is needed here).
#
# WARNING: this is UNFILTERED. It removes ALL operational rows in prod, not
# just synthetic data. customers/api_keys/users/config survive; every alert,
# case, dedup and audit_events row does not.
#
# SAFETY: dry-run by default. It only deletes when you pass --live, and a live
# run still requires the confirm string the workflow enforces.
#
# Usage:
#   ./purge-prod.sh             # DRY RUN: prints what would be purged, deletes nothing
#   ./purge-prod.sh --live      # LIVE: actually truncates operational tables in prod
#
# Requirements: gh (authenticated), the purge-data.yml workflow present on main.
#
# See ../README.md for the full story, the exit-code map, and why we skip the
# dedup flush (the prod dedup engine 500s; keys self-expire via a 30-min TTL).

set -euo pipefail

REPO="tekstream-cip/apollo"
WORKFLOW="purge-data.yml"
REF="main"
ENVIRONMENT="production"    # this script is LOCKED to production on purpose
SCOPE="operational"         # never 'full' here (full wipes customers/api_keys/users)
SKIP_DEDUP="true"           # REQUIRED in prod today: the prod dedup engine returns
                            # HTTP 500 on /api/dedup/admin/flush. Skipping is safe
                            # because dedup keys carry a 30-minute TTL (self-heal).

LIVE=0
for arg in "$@"; do
  case "$arg" in
    --live) LIVE=1 ;;
    -h|--help) sed -n '2,34p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done

command -v gh >/dev/null || { echo "ERROR: gh CLI not found." >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "ERROR: gh not authenticated. Run: gh auth login" >&2; exit 1; }

if [ "$LIVE" = "1" ]; then
  DRY_RUN="false"
  CONFIRM="PURGE ${ENVIRONMENT}"     # workflow requires this exact string on a live run
  echo "*** LIVE purge of PRODUCTION OPERATIONAL data. This DELETES rows and is irreversible. ***"
  # A deliberate 5s pause so an accidental --live can still be Ctrl-C'd.
  echo "Starting in 5s... (Ctrl-C to abort)"; sleep 5
else
  DRY_RUN="true"
  CONFIRM=""                          # ignored in dry-run
  echo "Dry run against PRODUCTION (no deletion). Pass --live to actually purge."
fi

echo "  scope=${SCOPE}  skip_dedup_flush=${SKIP_DEDUP}  ref=${REF}"

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
  echo "Purge did NOT succeed. Read the exit-code map in ../README.md (esp. 10-30 = flush aborted BEFORE truncate, so no rows were deleted; 40-49 = DB/bootstrap fault, also before truncate)." >&2
  exit 1
fi
echo "OK: production operational purge finished (dry_run=${DRY_RUN})."
