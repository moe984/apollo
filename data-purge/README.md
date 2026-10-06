# Apollo operational data purge (dev + prod)

Reusable scripts and runbook for clearing Apollo's **operational** data (seeded
or synthetic alerts, cases, dedup rows, etc.) from **dev** and **production**,
while keeping customers, api_keys, users and config.

Everything runs through the existing GitHub Actions workflow
`apollo/.github/workflows/purge-data.yml` (OIDC to AWS). You only need `gh`
authenticated locally. No direct AWS credentials, by design.

```
data-purge/
  README.md            <- this file (the runbook)
  dev/purge-dev.sh     <- dev purge (dry-run by default; --live to delete)
  prod/purge-prod.sh   <- prod purge (dry-run by default; --live to delete)
```

## Quick start

```bash
# DEV: preview first, then purge
./dev/purge-dev.sh                 # dry run, deletes nothing
./dev/purge-dev.sh --live          # actually truncates operational tables

# PROD: preview first, then purge
./prod/purge-prod.sh               # dry run, deletes nothing
./prod/purge-prod.sh --live        # actually truncates operational tables (irreversible)
```

Each script is locked to its own environment so a dev habit cannot fire at
prod. They dry-run by default and only delete with `--live`.

## What "operational" scope does

TRUNCATE (with `RESTART IDENTITY CASCADE`) of these tables, and only these:

```
rollback_records, response_feedback, response_approvals, response_actions,
investigation_feedback, investigation_steps, triage_feedback, dedup_feedback,
dedup_fingerprints, case_notes, case_alerts, playbook_versions,
response_results, investigation_results, triage_results, cases, playbooks,
alert_groups, llm_interactions, audit_events, metric_snapshots,
rule_health_snapshots, dead_letter_jobs, alerts
```

KEEPS: `customers`, `api_keys` (including the `soar_gateway` key SOAR pushes
with), `users`, and all config/settings.

This is **unfiltered**: it removes ALL operational rows, not just synthetic
ones. Use it when prod holds only seed/synthetic data, or when a full
operational reset is what you want.

> Never use `scope=full` for this. `full` truncates EVERY table, including
> customers and api_keys, and breaks SOAR ingestion + login until the tenant is
> re-provisioned. These scripts intentionally do not expose it.

## The dedup flush, and why we skip it

The workflow's default (safe) behavior for operational scope is to flush each
tenant's dedup-engine state BEFORE truncating, so a later re-ingest is not
matched against a now-deleted "ghost" canonical (apollo#501). If the flush
fails, the workflow aborts **before deleting any rows**.

We skip that flush (`skip_dedup_flush=true`) for two reasons:

1. **Prod engine is broken.** `cip-prod-ml-service` (created manually at
   go-live, not Terraform-managed) returns HTTP 500 on
   `/api/dedup/admin/flush`. It runs an image that predates the tenant-scoped
   flush added in cip#970. Until it is redeployed, the flush blocks the purge.
2. **The risk is bounded and self-heals.** Every dedup key is written with a
   30-minute TTL (`ml-service .../dedup/redis_client.py`, `ttl_minutes=30`).
   So skipping the flush only risks a stale match for up to 30 minutes, after
   which Redis is clean automatically.

Practical rule: if you need clean dedup immediately after a purge, do not
re-ingest into that tenant for 30 minutes. Otherwise, skipping is fine.

Relaxing the guard that used to block `skip_dedup_flush` under operational
scope was done in **apollo#567**.

`dev/purge-dev.sh` accepts `--flush` to opt back into the real flush (the dev
engine is healthy, so it returns 200). Prod has no such option today because
the prod engine 500s.

## The snapshot gate

A live purge writes a pre-truncate snapshot of `customers` + `api_keys` to
`PURGE_SNAPSHOT_BUCKET`, and refuses to run if that snapshot cannot be written.
No such bucket is configured, so the scripts pass `acknowledge_no_snapshot=true`.
For **operational** scope this is harmless: customers and api_keys are not
truncated, so there is nothing a snapshot would protect. (It would matter for
`scope=full`, which we never use here.)

## Parameters sent to the workflow

| Input | dev / prod value | Why |
|-------|------------------|-----|
| `environment` | `dev` / `production` | target env (selects cluster, task def, DB) |
| `dry_run` | `true` unless `--live` | dry-run prints row counts, deletes nothing |
| `scope` | `operational` | truncate operational tables, keep customers/api_keys/config |
| `skip_dedup_flush` | `true` | bypass the 500-ing flush; safe via 30-min TTL |
| `acknowledge_no_snapshot` | `true` | pass the snapshot gate (harmless for operational) |
| `confirm` | `PURGE <env>` on live | workflow's required live-purge confirmation |

## Exit-code map (from the ECS purge task)

The purge task's stdout is not readable from the purge role, so the container
exit code is the diagnosis. Codes below 50 all fire BEFORE any TRUNCATE, so a
non-zero exit in these ranges means **no rows were deleted**.

| Code | Meaning |
|------|---------|
| 0 | success (`PURGE_COMPLETE`, truncate committed) |
| 10 | dedup flush aborted: could not read customers (DB perm / RLS) |
| 11 | dedup flush aborted: could not reach the dedup engine (network) |
| 20/21/23/24/25/30 | dedup flush reached but rejected: HTTP 400/401/403/404/405/500 |
| 12 | dedup flush rejected, uncategorized non-2xx |
| 41 | `DATABASE_URL` unset/empty on the task definition |
| 42 | `pg` module missing in the image |
| 43 | could not reach the database (route/SG/DNS/timeout) |
| 44 | database auth failed |
| 45 | target database does not exist |
| 46 | permission denied reading the catalog |
| 47 | TLS/SSL negotiation to the database failed |

With `skip_dedup_flush=true` the 10-30 flush codes cannot occur (no flush runs).

## Follow-up worth doing

Redeploy `cip-prod-ml-service` from current `main` (via the `deploy-ml-service`
workflow in the `cip` repo, `environment=prod`) so the tenant-scoped flush
(cip#970) works again. After that, prod can run a "proper" purge without the
skip escape hatch (drop `skip_dedup_flush`, or set it false).

## History

- 2026-08-12: dev purge failed with a bare exit 1: schema drift — 12 of the 24
  hardcoded operational tables (rollback_records, response_*, investigation_*,
  triage_*, dedup_feedback, alert_groups) no longer exist in dev, and one
  missing relation (42P01) aborts the whole TRUNCATE. No rows were deleted.
  Fixed by filtering the list against pg_tables (apollo#766); purge then
  succeeded from the fix branch:
  https://github.com/tekstream-cip/apollo/actions/runs/31624827947
- 2026-07-24: first successful operational purge of dev and prod via this path.
  - dev run: https://github.com/tekstream-cip/apollo/actions/runs/30113213284
  - prod run: https://github.com/tekstream-cip/apollo/actions/runs/30113325660
  - Tracking issue: https://github.com/tekstream-cip/apollo/issues/522
  - Guard-relaxation PR: https://github.com/tekstream-cip/apollo/pull/567
  - Earlier prod attempts (13:41, 13:51 UTC) failed at the flush with HTTP 500
    and deleted nothing, which is what prompted the skip path.
