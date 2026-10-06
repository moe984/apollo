#!/usr/bin/env python3
"""
SOAR Dev Alert Replay

Pulls containers from Splunk SOAR dev and replays them into Apollo's
webhook endpoint. Used in the purge-and-test cycle for dedup engine
benchmarking.

Usage:
    # Dry run (fetch and count containers without posting)
    python replay.py --dry-run

    # Live replay (last 30 days, max 500 containers)
    python replay.py

    # Custom date range and limit
    python replay.py --since-days 7 --max-containers 200

    # Explicit credentials
    python replay.py --soar-token <token> --webhook-secret <secret>

    # Fetch secrets from AWS (OIDC/SSO)
    python replay.py --from-aws
"""
import argparse
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

try:
    import requests
except ImportError:
    print("requests not installed. Run: pip install requests")
    sys.exit(1)


# CEF field name mapping: SOAR artifact CEF keys -> Splunk adapter field names.
# The Splunk adapter extracts IOCs from src_ip, dest_ip, user, file_hash, url.
# SOAR artifacts use CEF conventions (sourceAddress, destinationAddress, etc).
CEF_TO_ADAPTER = {
    # IP addresses
    "sourceAddress": "src_ip",
    "src": "src_ip",
    "src_ip": "src_ip",
    "ipAddress": "src_ip",
    "ClientIP": "src_ip",
    "destinationAddress": "dest_ip",
    "dest": "dest_ip",
    "dest_ip": "dest_ip",
    "destinationIpAddress": "dest_ip",
    "dst": "dest_ip",
    # Users
    "sourceUserName": "user",
    "suser": "user",
    "user": "user",
    "userPrincipalName": "user",
    "accountName": "user",
    "destinationUserName": "user",
    "src_user": "src_user",
    # Hostnames
    "sourceHostName": "src_host",
    "destinationHostName": "dest_host",
    "dhost": "dest_host",
    "dest_host": "dest_host",
    "Computer_Name": "dest_host",
    "WorkstationName": "dest_host",
    # File hashes
    "fileHash": "file_hash",
    "file_hash": "file_hash",
    "SHA256String": "file_hash",
    "MD5String": "file_hash",
    # URLs
    "requestURL": "url",
    "request": "url",
    "url": "url",
    "Login_Url": "url",
    # Explicit IOC field (rare, ~4% of artifacts)
    "IOC": "IOC",
}

SOAR_URL_DEFAULT = "https://dev-soar.mdr.tekstream.com"
APOLLO_WEBHOOK_DEFAULT = "https://apollo-dev.tekstreampoc.com/api/v1/alerts/webhook"
PAGE_SIZE = 100


def fetch_soar(session, soar_url, path):
    """GET from the SOAR REST API."""
    url = f"{soar_url}{path}"
    resp = session.get(url)
    resp.raise_for_status()
    return resp.json()


def post_to_apollo(session, webhook_url, webhook_secret, payload):
    """POST a webhook payload to Apollo with CIP HMAC auth."""
    body = json.dumps(payload, separators=(",", ":"))
    timestamp = str(int(time.time()))
    signature = hmac.new(
        webhook_secret.encode(),
        f"{timestamp}.{body}".encode(),
        hashlib.sha256,
    ).hexdigest()

    resp = session.post(
        webhook_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-cip-signature": signature,
            "x-cip-timestamp": timestamp,
        },
    )
    return resp.status_code, resp.text


def fetch_containers(session, soar_url, since_iso, max_containers):
    """Paginate through SOAR containers created after since_iso."""
    all_containers = []
    page = 0

    while True:
        path = (
            f"/rest/container"
            f"?_filter_create_time__gt={quote(f'{since_iso}')}"
            f"&page_size={PAGE_SIZE}"
            f"&page={page}"
            f"&sort=create_time&order=asc"
        )
        print(f"  Fetching page {page}...")
        result = fetch_soar(session, soar_url, path)
        containers = result.get("data", [])
        print(f"    Got {len(containers)} containers")

        all_containers.extend(containers)

        if len(containers) < PAGE_SIZE:
            break
        if 0 < max_containers <= len(all_containers):
            all_containers = all_containers[:max_containers]
            break
        page += 1

    return all_containers


def fetch_artifacts(session, soar_url, container_id):
    """Fetch artifacts for a SOAR container (best-effort)."""
    try:
        result = fetch_soar(
            session, soar_url,
            f"/rest/container/{container_id}/artifacts?page_size={PAGE_SIZE}",
        )
        return result.get("data", [])
    except Exception:
        return []


def build_webhook_payload(container, artifacts, soar_url):
    """Build an Apollo webhook payload from a SOAR container and its artifacts."""
    # Merge artifact CEF fields and map to adapter-expected names
    result = {}
    for art in artifacts:
        cef = art.get("cef") or {}
        # Preserve original CEF fields
        result.update(cef)
        # Map to adapter-expected field names
        for cef_key, adapter_key in CEF_TO_ADAPTER.items():
            if cef_key in cef and adapter_key not in result:
                result[adapter_key] = cef[cef_key]

    result["search_name"] = container.get("name", "")
    result["severity"] = container.get("severity", "medium")
    result["container_id"] = container.get("id")
    result["source_data_identifier"] = container.get("source_data_identifier", "")

    sid = container.get("source_data_identifier") or f"soar-container-{container['id']}"

    return {
        "vendor": "splunk",
        "customer_id": "CUST-LSUAM",
        "payload": {
            "sid": sid,
            "search_name": container.get("name") or f"soar-container-{container['id']}",
            "results_link": f"{soar_url}/mission/{container['id']}",
            "owner": container.get("owner_name", "automation"),
            "app": "SplunkSOAR",
            "severity": container.get("severity", "medium"),
            "result": result,
        },
    }


def get_secrets_from_aws():
    """Fetch SOAR token and webhook secret from AWS."""
    secrets = {}

    # Webhook secret from Secrets Manager
    try:
        result = subprocess.run(
            [
                "aws", "secretsmanager", "get-secret-value",
                "--secret-id", "apollo/cip-credentials",
                "--region", "us-east-1",
                "--query", "SecretString",
                "--output", "text",
            ],
            capture_output=True, text=True, check=True,
        )
        creds = json.loads(result.stdout.strip())
        secrets["webhook_secret"] = creds.get("cip_webhook_secret", "")
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        print(f"[ERROR] Failed to fetch webhook secret: {e}")
        sys.exit(1)

    # SOAR token from gateway task definition
    try:
        result = subprocess.run(
            [
                "aws", "ecs", "describe-task-definition",
                "--task-definition", "mdr-plus-gateway",
                "--region", "us-east-1",
                "--query",
                "taskDefinition.containerDefinitions[0].environment[?name=='SOAR_POLLER_TOKEN'].value",
                "--output", "text",
            ],
            capture_output=True, text=True, check=True,
        )
        token = result.stdout.strip()
        if not token or token == "None":
            # Fallback to SOAR_TOKEN
            result = subprocess.run(
                [
                    "aws", "ecs", "describe-task-definition",
                    "--task-definition", "mdr-plus-gateway",
                    "--region", "us-east-1",
                    "--query",
                    "taskDefinition.containerDefinitions[0].environment[?name=='SOAR_TOKEN'].value",
                    "--output", "text",
                ],
                capture_output=True, text=True, check=True,
            )
            token = result.stdout.strip()
        secrets["soar_token"] = token
    except subprocess.CalledProcessError as e:
        print(f"[ERROR] Failed to fetch SOAR token: {e}")
        sys.exit(1)

    return secrets


def main():
    parser = argparse.ArgumentParser(description="Replay SOAR dev alerts into Apollo")
    parser.add_argument(
        "--soar-url",
        default=os.environ.get("SOAR_URL", SOAR_URL_DEFAULT),
        help=f"SOAR base URL (default: {SOAR_URL_DEFAULT})",
    )
    parser.add_argument(
        "--soar-token",
        default=os.environ.get("SOAR_TOKEN"),
        help="SOAR ph-auth-token (default: SOAR_TOKEN env var)",
    )
    parser.add_argument(
        "--webhook-url",
        default=os.environ.get("APOLLO_WEBHOOK_URL", APOLLO_WEBHOOK_DEFAULT),
        help=f"Apollo webhook URL (default: {APOLLO_WEBHOOK_DEFAULT})",
    )
    parser.add_argument(
        "--webhook-secret",
        default=os.environ.get("WEBHOOK_SECRET"),
        help="Apollo CIP webhook secret (default: WEBHOOK_SECRET env var)",
    )
    parser.add_argument(
        "--from-aws",
        action="store_true",
        help="Fetch SOAR token and webhook secret from AWS",
    )
    parser.add_argument(
        "--since-days",
        type=int,
        default=30,
        help="Fetch containers created in the last N days (default: 30)",
    )
    parser.add_argument(
        "--max-containers",
        type=int,
        default=500,
        help="Max containers to replay, 0 = unlimited (default: 500)",
    )
    parser.add_argument(
        "--delay-ms",
        type=int,
        default=200,
        help="Delay between webhook posts in ms (default: 200)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and count containers without posting to Apollo",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show per-container details during replay",
    )
    args = parser.parse_args()

    # Resolve credentials
    soar_token = args.soar_token
    webhook_secret = args.webhook_secret

    if args.from_aws:
        print("[INFO] Fetching secrets from AWS...")
        aws_secrets = get_secrets_from_aws()
        soar_token = soar_token or aws_secrets.get("soar_token")
        webhook_secret = webhook_secret or aws_secrets.get("webhook_secret")

    if not soar_token:
        print("[ERROR] No SOAR token. Use --soar-token, --from-aws, or set SOAR_TOKEN.")
        sys.exit(1)

    if not args.dry_run and not webhook_secret:
        print("[ERROR] No webhook secret. Use --webhook-secret, --from-aws, or set WEBHOOK_SECRET.")
        sys.exit(1)

    since = datetime.now(timezone.utc) - timedelta(days=args.since_days)
    since_iso = since.isoformat()

    print(f"\n{'=' * 50}")
    print(f"  SOAR Alert Replay")
    print(f"{'=' * 50}")
    print(f"  SOAR:       {args.soar_url}")
    print(f"  Apollo:     {args.webhook_url}")
    print(f"  Since:      {since_iso} ({args.since_days} days)")
    print(f"  Max:        {args.max_containers or 'unlimited'}")
    print(f"  Delay:      {args.delay_ms}ms")
    print(f"  Mode:       {'DRY RUN' if args.dry_run else 'LIVE'}")
    print()

    # Setup SOAR session
    soar_session = requests.Session()
    soar_session.headers.update({
        "ph-auth-token": soar_token,
        "Accept": "application/json",
    })

    # Fetch containers
    print("Fetching containers from SOAR...")
    containers = fetch_containers(
        soar_session, args.soar_url, since_iso, args.max_containers,
    )
    print(f"\nTotal containers fetched: {len(containers)}")

    if not containers:
        print("\n[OK] No containers found in the specified date range.")
        return

    # Show summary
    severity_counts = Counter(c.get("severity", "unknown") for c in containers)
    label_counts = Counter(c.get("label", "unknown") for c in containers)

    print(f"\n--- By severity ---")
    for sev, count in severity_counts.most_common():
        print(f"  {sev}: {count}")

    print(f"\n--- By label ---")
    for label, count in label_counts.most_common():
        print(f"  {label}: {count}")

    if args.dry_run:
        print("\n[DRY RUN] No data posted to Apollo.")
        return

    # Live replay
    apollo_session = requests.Session()
    stats = {"posted": 0, "failed": 0, "deduplicated": 0}

    print(f"\nReplaying {len(containers)} containers...\n")

    for i, container in enumerate(containers):
        artifacts = fetch_artifacts(soar_session, args.soar_url, container["id"])
        payload = build_webhook_payload(container, artifacts, args.soar_url)

        try:
            status, body = post_to_apollo(
                apollo_session, args.webhook_url, webhook_secret, payload,
            )
            if status == 201:
                stats["posted"] += 1
            elif status == 200:
                stats["deduplicated"] += 1
                stats["posted"] += 1
            else:
                stats["failed"] += 1
                if args.verbose or stats["failed"] <= 5:
                    print(f"  WARN: container {container['id']} HTTP {status}: {body[:100]}")
        except Exception as e:
            stats["failed"] += 1
            if args.verbose or stats["failed"] <= 5:
                print(f"  ERROR: container {container['id']}: {e}")

        # Progress
        if (i + 1) % 50 == 0 or i == len(containers) - 1:
            print(
                f"  [{i + 1}/{len(containers)}] "
                f"posted={stats['posted']} "
                f"dedup={stats['deduplicated']} "
                f"failed={stats['failed']}"
            )

        if args.delay_ms > 0 and i < len(containers) - 1:
            time.sleep(args.delay_ms / 1000)

    print(f"\n{'=' * 50}")
    print(f"  Replay Complete")
    print(f"{'=' * 50}")
    print(f"  Total:        {len(containers)}")
    print(f"  Posted:       {stats['posted']}")
    print(f"  Deduplicated: {stats['deduplicated']}")
    print(f"  Failed:       {stats['failed']}")


if __name__ == "__main__":
    main()
