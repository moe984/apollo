# SOAR Dev Alert Pull

Pulls containers from Splunk SOAR dev and replays them into Apollo's webhook endpoint. Used in the purge-and-test cycle for dedup engine benchmarking.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Usage

```bash
# Dry run (fetch and show container summary)
python replay.py --dry-run

# Live replay (last 30 days, max 500 containers)
python replay.py

# Custom date range and limit
python replay.py --since-days 7 --max-containers 200

# Slower rate (500ms between posts)
python replay.py --delay-ms 500

# Verbose (show per-container details)
python replay.py -v

# Explicit credentials
python replay.py --soar-token <token> --webhook-secret <secret>

# Fetch secrets from AWS (requires AWS CLI + credentials)
python replay.py --from-aws
```

## Credentials

The script needs two secrets:

| Secret | Flag | Env var | AWS source |
|--------|------|---------|------------|
| SOAR API token | `--soar-token` | `SOAR_TOKEN` | Gateway task def `SOAR_POLLER_TOKEN` |
| Apollo webhook secret | `--webhook-secret` | `WEBHOOK_SECRET` | Secrets Manager `apollo/cip-credentials` |

Use `--from-aws` to fetch both automatically (requires active AWS session).

## CEF field mapping

SOAR artifacts use CEF field names. The script maps them to the names Apollo's Splunk adapter expects for IOC extraction:

| SOAR CEF | Apollo adapter |
|----------|---------------|
| sourceAddress, ipAddress | src_ip |
| destinationAddress, destinationIpAddress | dest_ip |
| sourceUserName, suser | user |
| destinationHostName, dhost | dest_host |
| fileHash | file_hash |
| requestURL, request | url |

## Purge-and-test cycle

1. **Purge:** `cd ../data-purge && python purge.py -v -y`
2. **Reload:** `python replay.py`
3. **Benchmark:** Check Apollo dashboard + dedup report

## Tracking

- tekstream-cip/apollo#338
