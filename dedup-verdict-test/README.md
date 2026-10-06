# Dedup verdict endpoint test

Exercises `GET /api/v1/dedup/verdict/{containerId}` (apollo#1044, shipped in
apollo#1045): parent or child role, DUPLICATE or SIMILAR verdict, the 7-character
entity fingerprints of the child and its root canonical parent, and the
`auto_close` / `review_required` recommendation.

## Folder

```
dedup-verdict-test/
├── README.md
├── verdict.py      ← test script (stdlib only)
└── logs/           ← one JSON line per request, verdict_<UTC date>.log
```

## Auth

`x-api-key`, the same key that posts to `POST /api/v1/ingest/soar`. The script
reads `APOLLO_API_KEY` from `../.env` (`poc/apollo/.env`, gitignored) or from
the environment. A `soar_gateway` key reads fleet-wide; a tenant-scoped key
reads only its own tenant.

## Usage

```bash
# Contract checks that need no known container: 400 / 401 / 404
python3 verdict.py --smoke

# One or more containers on prod (default)
python3 verdict.py 990008 990009

# Same on dev
python3 verdict.py --env dev 990008

# Assert a status
python3 verdict.py --expect 404 123456
```

Exit code 1 when any response fails the contract check.

## Status codes

| Status | Meaning |
|--------|---------|
| 200 | verdict returned |
| 400 | container id is not numeric (`container_id_invalid`) |
| 401 | missing, invalid, revoked or expired key |
| 403 | tenant-scoped key with no tenant binding (`key_not_tenant_bound`) |
| 404 | no alert for that container (`container_not_found`) |
| 409 | id matches more than one alert (`container_ambiguous`), use a tenant-scoped key |
| 422 | parent chain cannot be walked to a canonical alert (`origin_unresolved`) |

## Response shape (200)

Parent (canonical):

```json
{ "container_id": "990008", "role": "parent", "verdict": "NEW",
  "parent": null, "child": null,
  "self": { "container_id": "990008", "entity_fingerprint": "826aba8", "child_count": 3 },
  "fingerprints_match": null, "recommendation": null,
  "reason": "This container is the canonical alert of its cluster." }
```

Child:

```json
{ "container_id": "990009", "role": "child", "verdict": "DUPLICATE",
  "parent": { "container_id": "990008", "entity_fingerprint": "826aba8" },
  "child":  { "container_id": "990009", "entity_fingerprint": "826aba8" },
  "fingerprints_match": true, "recommendation": "auto_close",
  "reason": "Child and parent entity fingerprints are identical." }
```

The match is decided on the full SHA-256; only the short form is returned. A
missing fingerprint on either side yields `review_required`.

## Hosts

| env | base URL |
|-----|----------|
| prod | `https://apollo.cosmos.tekstream.com` (host rule on `mdr-plus-prod-alb`, service `cip-prod-apollo`) |
| dev | `https://apollo-dev.cosmos.tekstream.com` |

`d2lh0brw6geryn.cloudfront.net` is the DEV CloudFront distribution (E65QHSV77UYVW), not prod.
A prod key sent there is rejected with 401 "Invalid or revoked API key" because it reaches
the dev database. Verified 2026-09-11 against the prod task log.
