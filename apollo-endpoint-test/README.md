# Apollo Webhook — Alert Ingestion Tests

Simulate sending alerts to Apollo's webhook endpoint using HMAC-SHA256 auth.
Tests the full ingestion pipeline: webhook → normalize → STIX → dedup → persist → route.

## Folder Structure

```
apollo-endpoint-test/
├── README.md              ← This file
├── .env                   ← Apollo URL + CIP webhook secret (gitignored)
├── test_webhook.py        ← Webhook ingestion test script
├── test-data/
│   └── dedup_input_test.csv  ← 11 real Splunk STIX bundles
└── logs/
    └── test_webhook_*.log    ← Test run logs
```

## Prerequisites

- `.env` file with:
  ```
  APOLLO_URL=https://d2lh0brw6geryn.cloudfront.net
  CIP_WEBHOOK_SECRET=<shared secret from Secrets Manager>
  ```
- Python 3.10+ (stdlib only)

## Usage

```bash
# Check Apollo health
python3 test_webhook.py --health

# Send one synthetic Splunk alert
python3 test_webhook.py

# Stream all 11 alerts from CSV
python3 test_webhook.py --all

# Stream first 3 alerts
python3 test_webhook.py --limit 3
```

## Auth

Apollo webhook supports three auth methods. This script uses **Method A (HMAC)**:

```
Signature = HMAC-SHA256(secret, "{unix_timestamp}.{json_body}")
Headers:
  X-CIP-Signature: <hex signature>
  X-CIP-Timestamp: <unix seconds>
```

The secret is the same CIP gateway shared secret stored in Secrets Manager.

## Expected Responses

| Status | Meaning |
|--------|---------|
| 201 | Alert ingested, ready for triage |
| 200 | Alert deduplicated (grouped with existing) |
| 202 | Alert quarantined (unattributable or CPQ unavailable) |
| 401 | Authentication failed |
| 400 | Invalid request body |
| 503 | Backpressure (too many concurrent ingestions) |
