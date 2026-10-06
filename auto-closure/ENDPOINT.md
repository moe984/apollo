# Apollo alert status: the update endpoints

Documented from apollo @ `34ae007f` (2026-09-21). Feature landed under apollo#1058,
history under apollo#1099.

## Short answer

```
PATCH /api/v1/alerts/{alertId}/workflow-status
```

Auth: `x-api-key`, the same key that posts to `POST /api/v1/ingest/soar`.
Body: `{"status": "auto_closed", "reason": "..."}`.

Base URLs: `https://apollo.cosmos.tekstream.com` (prod),
`https://apollo-dev.cosmos.tekstream.com` (dev).

This is the endpoint `auto_close.py` in this folder calls.

## There are two routes, one writer

| Route | Auth | Actor recorded |
|-------|------|----------------|
| `PATCH /api/v1/alerts/{alertId}/workflow-status` | `x-api-key` (webhookAuth) | `apikey:<id>`, actor_type `system` |
| `PATCH /api/v1/dashboard/alerts/{id}/workflow-status` | CIP SSO cookie or API key, role `soc_analyst` | Entra OID + `preferred_username`, actor_type `human` |

Both hand the write to one service, `src/services/alert-workflow-status.ts`, so a
status set by automation appears on the alert page and the Alert Queue exactly as an
analyst's own change would, and vice versa.

Source:
- `apollo/src/routes/alert-workflow-status.ts` (API route)
- `apollo/src/routes/dashboard/alerts.ts` (dashboard twin, `PATCH /alerts/:id/workflow-status`)
- `apollo/src/services/alert-workflow-status.ts` (the single writer)
- `apollo/src/db/schema/alerts.ts` (`ALERT_WORKFLOW_STATUSES`)

## Statuses

`ALERT_WORKFLOW_STATUSES`, in schema order:

```
open
preparing
under_review
under_investigation
escalated
customer_responded
closed
auto_closed
```

Rules, per operator decision 2026-09-14:

- Every alert is created `open` (`DEFAULT_WORKFLOW_STATUS`).
- Any status may be set from any other. No terminal status, no role gate beyond
  `soc_analyst` on the dashboard route. An `auto_closed` alert can be set back to `open`.
- This is an update endpoint, not a state machine. The caller names the status,
  the server records it.
- The engine-owned `status` column is NEVER touched by this path. Only the four
  `workflow_status*` columns plus `updated_at` are written, and a contract test pins that.

## Request

```
PATCH /api/v1/alerts/{alertId}/workflow-status
x-api-key: <key>
content-type: application/json

{
  "status": "auto_closed",
  "reason": "duplicate of njit-231940"
}
```

- `alertId` is the **Apollo alert id**: the value in the queue's Container ID column
  and in the detail page URL (`/dashboard/alerts/<id>`). It is NOT the bare SOAR
  container id that `/api/v1/dedup/verdict` takes.
- `status` required. Validated by hand so an unknown value returns the endpoint's own
  `status_invalid` envelope rather than the generic validator 400.
- `reason` optional, max 1000 chars. Stored as `workflow_status_reason`.

Scope: a `soar_gateway`-scoped key reaches every tenant's alerts; a tenant-scoped key
reaches its own. A tenant-scoped key with no tenant binding gets 403.

## Response 200

```json
{
  "alert_id": "mccrary-7802",
  "changed": true,
  "previous_status": "open",
  "status": "auto_closed",
  "changed_at": "2026-09-21T14:02:11.412Z",
  "changed_by": "apikey:7f3c...",
  "reason": "duplicate of njit-231940"
}
```

`changed: false` means the alert was already in that status and nothing was written.
`previous_status` is a plain string, not the enum: a value written by a newer backend
mid-rollout still serializes rather than 500ing.

The dashboard twin returns the same facts under a `data` envelope with the dashboard's
field names (`workflow_status`, `workflow_status_changed_at`, `workflow_status_changed_by`,
`workflow_status_reason`).

## Errors

| Code | `code` | Meaning |
|------|--------|---------|
| 400 | `status_invalid` | `status` is not one of the eight values |
| 400 | (platform validation body) | body failed schema, e.g. missing `status`, `reason` over 1000 chars |
| 401 | (gate's own `{error, message}`) | missing, invalid, revoked or expired key |
| 403 | `key_not_tenant_bound` | tenant-scoped key with no tenant binding |
| 404 | `alert_not_found` | no alert with that id in scope for this key |
| 409 | `status_conflict` | another writer changed the alert between read and write; retry against the current status |

The 400/403/404/409 bodies carry `code`, `alert_id` and `correlationId`.

## Side effects of a successful change

1. The four workflow columns on the alert row are updated, guarded on the status the
   lookup saw (that guard is what produces the 409).
2. One `audit_events` row, `event_type = alert_workflow_status_changed`, written INSIDE
   the same transaction, so a status can never exist without the entry explaining it.
   (The 2026-09-14 "no audit row" decision was reversed 2026-09-18: the alert row holds
   only the latest change, so without history a close followed by a reopen left no trace.)
3. A dashboard SSE event `alert:status` for live update.
4. A Cosmos bus event `apollo.alert.workflow_status_changed`, spoke `SP-015`, payload
   `{alert_id, customer_id, previous_status, status, reason, actor, actor_type, via}`.
   Emission failure is logged and non-fatal; the change is already committed.

`via` is `api` for the key route and `dashboard` for the SSO route.

## Reading the history back

```
GET /api/v1/dashboard/alerts/{id}/workflow-history
```

SSO / `soc_analyst`, no API-key-gate twin. Returns the alert's arrival plus every
recorded change, oldest first, capped at 200 with a `truncated` flag. The raw actor id
is never served: each event carries `actor_kind`, and `actor_name` only when the kind is
`user`. Alerts that predate the history writer return zero events, which is intended and
not an error.

## curl

```bash
curl -sS -X PATCH \
  "https://apollo.cosmos.tekstream.com/api/v1/alerts/mccrary-7802/workflow-status" \
  -H "x-api-key: $APOLLO_AUTO_CLOSURE_API_KEY" \
  -H "content-type: application/json" \
  -d '{"status":"auto_closed","reason":"POC auto-closure run"}'
```

Reopen:

```bash
curl -sS -X PATCH \
  "https://apollo.cosmos.tekstream.com/api/v1/alerts/mccrary-7802/workflow-status" \
  -H "x-api-key: $APOLLO_AUTO_CLOSURE_API_KEY" \
  -H "content-type: application/json" \
  -d '{"status":"open","reason":"reverting POC change"}'
```

Swagger: `https://apollo.cosmos.tekstream.com/docs`, tag **SOAR Push Gate**.

## Sample client to hand to the SOAR engineer

`soar_set_workflow_status.py` in this folder. Standard library only (no `requests`,
no `dotenv`), so it drops into any SOAR Python runtime unchanged. `set_workflow_status()`
is the part to copy into a playbook; the CLI wrapper below it is for trying the call by
hand first.

It covers the things a playbook gets wrong: the alert-id vs container-id distinction,
409 retry (an analyst moved the alert mid-flight), 5xx and transport retry with backoff,
no retry on 4xx, the `changed: false` no-op case, and capturing `x-correlation-id` on
every response including success.

```bash
export APOLLO_API_KEY=...
python3 soar_set_workflow_status.py --alert-id mccrary-7802 --status auto_closed \
    --reason "Duplicate of njit-231940, auto-closed by SOAR playbook X" --dry-run
```
