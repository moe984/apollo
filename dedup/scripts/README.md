# Dedup Data Pull Scripts

## How it works

Same pattern as `false-positive-predictor/fetch_alerts.py`:

```
fetch_dedup_data.py (local)
        │
        ▼
gh workflow run extract-dedup-data.yml  (tekstream-cip/apollo)
        │
        ▼
GitHub OIDC → AWS STS → assume AWS_DEPLOY_ROLE_ARN
        │
        ▼
Secrets Manager → fetch apollo/database-url
        │
        ▼
ECS Fargate task (inside VPC) → query Postgres directly
        │
        ▼
CSV output → CloudWatch logs → workflow artifact
        │
        ▼
gh run download → local CSV files in dedup/data/
```

## Prerequisites

- `gh` CLI installed and authenticated: `brew install gh && gh auth login`
- Access to `tekstream-cip/apollo` repo

## Usage

```bash
cd /Users/mohammad.yekrangian/Projects/poc/apollo-ai-ml/dedup/scripts

# Pull all dedup datasets (default)
python fetch_dedup_data.py

# Pull specific dataset
python fetch_dedup_data.py --mode dedup_decisions

# Custom customer and limit
python fetch_dedup_data.py --customer CUST-LSUAM --limit 500

# Download from an existing workflow run
python fetch_dedup_data.py --run-id 12345

# Use a specific branch for the workflow
python fetch_dedup_data.py --ref main
```

## Output Files

| File | Contents | Rows |
|------|----------|------|
| `dedup_input.csv` | All alerts with `stix_bundle`, `raw_payload`, `dedup_metadata`, `parent_alert_id` | Up to --limit |
| `dedup_decisions.csv` | Deduplicated + pending_review alerts joined with their parent alert | Dedup'd alerts only |
| `dedup_feedback.csv` | Analyst corrections to dedup decisions (`corrected_disposition`, `similarity`, etc.) | Feedback rows only |
| `fingerprints.csv` | IOC fingerprints per alert (`ioc_type`, `ioc_values`) | Up to --limit |

## Datasets Explained

### `dedup_input` — What enters the dedup pipeline
Every alert row with the full STIX bundle and raw payload. This is the data that
both dedup layers consume. Use this to replay dedup decisions offline or train models.

### `dedup_decisions` — What dedup decided
Only alerts where dedup took action (status = deduplicated or pending_review) or
that are canonical alerts (dedup_count > 0). Joined with the parent alert so you
can see both the duplicate and the original side by side.

### `dedup_feedback` — Analyst corrections
Rows from `dedup_feedback` table where analysts marked a dedup decision as
correct, false, or missed. This is the ground truth for tuning thresholds.

### `fingerprints` — IOC fingerprints stored per alert
From the `dedup_fingerprints` table. Each row has an alert ID, IOC type
(ip, domain, file_hash, etc.), and the array of canonical IOC values extracted
from that alert's STIX bundle. This is what the IOCIndex uses for candidate
discovery.
