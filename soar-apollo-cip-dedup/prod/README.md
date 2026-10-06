# Apollo synthetic alert engine (prod)

One script, `synthetic_alert_engine.py`, that generates synthetic SOAR alerts
and pushes them into Apollo through the real production ingest gate, then reads
back the CIP dedup verdict SOAR would branch on.

```
SOAR-shaped event  --POST /api/v1/ingest/soar-->  Apollo
   transform to STIX -> store -> CIP ml-service dedup -> verdict -> response
```

> Prod injects real alerts into the live SOC. Only run against prod when that
> is intended. It prints a 5-second countdown first (skip with `--yes`).

## What it generates (every run)

| Attribute | Value |
|---|---|
| Clients | 3: `LSU`, `FTCC`, `MCNEESE` (`--clients`) |
| Severities | 4: `critical`, `high`, `medium`, `low` (cycled) |
| Alert types | 10 distinct rule names / detections (cycled) |
| IOC slots | 6 CEF observables (below) |
| Verdicts | `NEW`, `DUPLICATE`, `SIMILAR`, produced deliberately |

### The 6 IOC slots

These are the CEF fields Apollo's `cef-to-alert` transform maps to STIX
observables (`apollo/src/ingestion/soar-artifact-poller/cef-to-alert.ts`):

| # | CEF field | Observable |
|---|---|---|
| 1 | `sourceAddress` | source IPv4 |
| 2 | `destinationAddress` | destination IPv4 |
| 3 | `sourceUserName` | user account |
| 4 | `destinationHostName` | hostname |
| 5 | `fileHashSha256` | SHA-256 file hash |
| 6 | `requestURL` | URL |

All values stay in reserved/private space so no real host is ever named:
RFC1918 `10.0.0.0/8` (source) and `172.16.0.0/12` (dest) for IPs, the RFC6761
reserved `.example` TLD for domains, and random synthetic hashes. Each alert
gets a unique per-alert token so its user and both registrable domains
(`corp<tok>.example`, `badnet<tok>.example`) are values no other alert shares.
This diversity is deliberate: the dedup layer-2 fuzzy match keys on shared
registrable domain / subnet, so reusing one domain or /24 across NEW alerts
made them fuzzy-match each other and collapse to SIMILAR.

### How each verdict is produced

- **NEW** — a fresh 6-slot IOC set with a unique rule name. First time the
  engine sees these observables, so it is a new canonical.
- **DUPLICATE** — a distinct SOAR container (own id + `source_data_identifier`)
  carrying the **identical** 6 IOCs and the **same** rule name as its canonical.
  Tier-1 content dedup links it to the canonical.
- **SIMILAR** — keeps 5 slots identical to the canonical (same IP, user, host,
  hash, dest IP), varies **one** (`requestURL`), and uses a **different** rule
  name per variant. The engine scores it fuzzy (layer-2) and returns SIMILAR.
  The distinct rule name is required for the SIMILAR verdict.

## Tenant key requirement (important)

Multi-client seeding needs a **`soar_gateway`-scoped** API key: the tenant is
read from each event (CEF `ClientName` / `container.tenant_name` / the
`x-client-name` header, all three of which the engine sets).

A **`tenant`-scoped** key (what has historically been in `.env`) ignores the
client name and files **everything under its own single tenant**, so the 3
clients collapse to 1. If you need all three, mint a `soar_gateway` key from a
soc_admin dashboard session (see apollo#537 / #554 / #559): Customers page ->
open the tenant -> create key, scope `soar_gateway`; copy the raw value into
`.env` as `APOLLO_API_KEY`.

## Usage

```bash
# Preview the full matrix, send nothing
python3 synthetic_alert_engine.py --dry-run

# Full run into prod (default: 10 new + 4 dup + 4 similar, per client = 54 alerts)
python3 synthetic_alert_engine.py

# One client, small batch, reproducible IOCs
python3 synthetic_alert_engine.py --clients LSU --new 5 --dup 3 --similar 3 --seed 42

# Only duplicates, no countdown
python3 synthetic_alert_engine.py --only dup --dup 10 --yes
```

Config comes from `./.env` (`APOLLO_URL`, `APOLLO_API_KEY`) or `--url` /
`--api-key`.

### Flags

| Flag | Default | Meaning |
|---|---|---|
| `--clients` | `LSU,FTCC,MCNEESE` | comma-separated tenant names |
| `--new` | `10` | NEW alerts per client (10 = one per alert type) |
| `--dup` | `4` | DUPLICATEs per client (share one canonical) |
| `--similar` | `4` | SIMILAR variants per client (share one canonical) |
| `--only` | (all) | `new` / `dup` / `similar` to generate just that group |
| `--seed` | run time | RNG seed for reproducible IOC values |
| `--sleep` | `0.1` | seconds between requests |
| `--dry-run` | off | build and print, send nothing |
| `--yes` | off | skip the prod confirmation countdown |

## Output

Per alert: the verdict returned vs the expected verdict, flagged on mismatch.
A summary tallies NEW / DUPLICATE / SIMILAR per client and overall, and reports
the mismatch count (exit code 1 if any verdict did not match expectation).

## Request shape

`POST {APOLLO_URL}/api/v1/ingest/soar`, headers `x-api-key` + `x-client-name`,
body:

```json
{
  "container": {
    "id": 900000123,
    "name": "Palo Alto - Critical Threat Allowed Inbound",
    "tenant_name": "LSU",
    "severity": "critical",
    "source_data_identifier": "lsu-new-1784900000-0"
  },
  "artifacts": [
    { "label": "artifact", "name": "network-observable", "cef": {
        "ClientName": "LSU",
        "sourceAddress": "198.51.100.42",
        "destinationAddress": "203.0.113.7",
        "sourceUserName": "user48213",
        "destinationHostName": "host-5561.corp.example",
        "fileHashSha256": "<64 hex>",
        "requestURL": "http://malware412.example.net/70413/payload"
    } }
  ]
}
```

## Related

- Purge the data this engine seeds: `../../data-purge/` (dev + prod operational
  purge scripts). Typical loop: purge -> seed with this engine -> validate.
- Contract source of truth: `apollo/src/ingestion/soar-artifact-poller/cef-to-alert.ts`
  and `apollo/src/ingestion/soar-gate/`.
