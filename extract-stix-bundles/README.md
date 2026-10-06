# Extract STIX Bundles

Extracts the STIX 2.1 bundles that Apollo sends to the ML-service dedup engine. Each bundle is saved as an individual JSON file with extracted IOCs for manual cross-validation.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
# Extract via GitHub Actions (no local AWS creds needed)
python extract.py --github

# Extract with dedup metadata for full context
python extract.py --github --full

# Filter by status
python extract.py --github --status deduplicated
python extract.py --github --status normalized

# Custom limit
python extract.py --github --limit 50

# Direct database (local dev or tunneled)
python extract.py --database-url postgresql://...
```

## Output

Each alert is saved as `output/stix_bundles/{alert_id}.json`:

```json
{
  "alert_id": "uuid-123",
  "source": "splunk",
  "severity": "HIGH",
  "status": "deduplicated",
  "created_at": "2026-05-29T12:00:00Z",
  "stix_bundle": {
    "type": "bundle",
    "objects": [
      { "type": "ipv4-addr", "value": "192.168.1.1" },
      { "type": "user-account", "user_id": "jdoe" },
      { "type": "indicator", "name": "Brute Force Login" }
    ]
  },
  "extracted_iocs": {
    "ips": ["192.168.1.1"],
    "domains": [],
    "urls": [],
    "file_hashes": [],
    "usernames": ["jdoe"],
    "alert_name": "Brute Force Login"
  },
  "ioc_counts": {
    "ips": 1, "domains": 0, "urls": 0,
    "file_hashes": 0, "usernames": 1, "alert_name": 1
  }
}
```

With `--full`, also includes `dedup_metadata`, `dedup_count`, and `parent_alert_id`.

## What to look for

- **Empty IOCs:** If `extracted_iocs` has all empty lists, the dedup engine has nothing to compare. The Splunk adapter failed to extract IOCs from the webhook payload.
- **Missing alert_name:** Without an indicator name, the dedup engine can't match by `alert_name` prerequisite.
- **Dedup metadata:** With `--full`, check `dedup_metadata.similarity_score`, `dedup_metadata.layer`, and `dedup_metadata.confidence_tier` to see what the engine decided.

## IOC extraction logic

The `extracted_iocs` field mirrors the ML-service's `stix_parser.py`:

| STIX Object Type | Extracted As |
|-------------------|-------------|
| `ipv4-addr` / `ipv6-addr` | `ips` |
| `domain-name` | `domains` |
| `url` | `urls` |
| `file` (with `.hashes`) | `file_hashes` |
| `user-account` | `usernames` |
| `indicator` (`.name`) | `alert_name` |

## Tracking

- tekstream-cip/apollo#338
