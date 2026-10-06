#!/usr/bin/env python3
"""
Apollo Dedup E2E — Production Validation (cip-command#699)

Single test script for the full dedup round-trip:

  CSV STIX bundles → Apollo webhook (HMAC) → Splunk adapter → STIX normalize
  → ml-service dedup engine (via CIP Gateway) → verdict → Apollo DB

The CSV contains intentional duplicates. The dedup engine should catch
them as they stream in sequentially: first alert is NEW, subsequent
duplicates are DUPLICATE.

Pre-flight:
  - Verifies dedup engine health via CIP Gateway
  - Flushes dedup engine state for clean test

Usage:
  python3 stream_dedup_e2e.py                   # Stream all CSV alerts
  python3 stream_dedup_e2e.py --limit 2          # First 2 alerts only
  python3 stream_dedup_e2e.py --no-flush         # Skip dedup state flush
"""

import argparse
import csv
import hashlib
import hmac
import http.client
import json
import logging
import os
import ssl
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(
    LOG_DIR, f"stream_dedup_e2e_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
)

logger = logging.getLogger("stream_dedup_e2e")
logger.setLevel(logging.DEBUG)

fh = logging.FileHandler(LOG_FILE)
fh.setLevel(logging.DEBUG)
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(fh)

ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(ch)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

env_file = Path(__file__).parent / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

APOLLO_URL = os.environ.get(
    "APOLLO_URL", "https://d2lh0brw6geryn.cloudfront.net"
).rstrip("/")
GATEWAY_URL = os.environ.get(
    "GATEWAY_URL", "https://d37lzn65ul43gr.cloudfront.net"
).rstrip("/")
WEBHOOK_SECRET = os.environ.get("CIP_WEBHOOK_SECRET", "")
CIP_API_KEY = os.environ.get("CIP_API_KEY", "")
CSV_INPUT = os.path.join(os.path.dirname(__file__), "test-data", "dedup_input_test.csv")

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def gateway_request(method, path, timeout=10):
    """Send a request to the CIP Gateway with API key auth."""
    url = f"{GATEWAY_URL}{path}"
    req = Request(url, method=method, headers={
        "X-API-Key": CIP_API_KEY, "Accept": "application/json",
    })
    try:
        resp = urlopen(req, timeout=timeout)
        return resp.status, json.loads(resp.read().decode())
    except HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"error": str(e)}
    except Exception as e:
        return 0, {"error": str(e)}


def webhook_send(body, timeout=30):
    """Sign and POST to Apollo webhook via HMAC-SHA256.

    Uses http.client directly to avoid urllib connection-reuse issues
    with CloudFront's Connection: close behavior.
    """
    parsed = urlparse(f"{APOLLO_URL}/api/v1/alerts/webhook")
    body_json = json.dumps(body, separators=(",", ":"))
    timestamp = str(int(time.time()))
    signing_payload = f"{timestamp}.{body_json}"
    signature = hmac.new(
        WEBHOOK_SECRET.encode(), signing_payload.encode(), hashlib.sha256
    ).hexdigest()

    headers = {
        "Content-Type": "application/json",
        "X-CIP-Signature": signature,
        "X-CIP-Timestamp": timestamp,
        "Connection": "close",
    }

    logger.debug("REQUEST  POST %s  body_len=%d", parsed.geturl(), len(body_json))
    start = time.time()
    try:
        ctx = ssl.create_default_context()
        conn = http.client.HTTPSConnection(parsed.hostname, timeout=timeout, context=ctx)
        conn.request("POST", parsed.path, body=body_json.encode(), headers=headers)
        resp = conn.getresponse()
        latency = int((time.time() - start) * 1000)
        raw = resp.read().decode()
        conn.close()
        if not raw.strip():
            logger.warning("Empty body (HTTP %d, %dms)", resp.status, latency)
            return resp.status, {"error": "empty response", "status": "?"}, latency
        data = json.loads(raw)
        logger.debug("RESPONSE %d  latency=%dms  body=%s", resp.status, latency, raw[:500])
        return resp.status, data, latency
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        logger.error("ERROR  latency=%dms  %s", latency, e)
        return 0, {"error": str(e), "status": "?"}, latency


# ---------------------------------------------------------------------------
# STIX bundle -> Splunk webhook payload
# ---------------------------------------------------------------------------


def wrap_as_splunk_webhook(alert_id, stix_bundle, severity, customer_id):
    """Wrap a STIX bundle into Splunk webhook format for Apollo's adapter.

    Extracts the vendor_specific notable_fields from the STIX indicator's
    extensions — these are the raw Splunk fields that the real webhook sends.
    This ensures the Splunk adapter sees the same data (ClientName, IOCs,
    search_name, etc.) as production.
    """
    indicator = next(
        (o for o in stix_bundle.get("objects", []) if o.get("type") == "indicator"), {}
    )
    extensions = indicator.get("extensions", {})
    ext = extensions.get("extension-definition--a0b1c2d3-e4f5-6789-abcd-ef0123456789", {})
    vendor_specific = ext.get("vendor_specific", {})
    notable_fields = vendor_specific.get("notable_fields", {})
    search_name = vendor_specific.get("search_name", indicator.get("name", f"Alert {alert_id}"))

    return {
        "vendor": "splunk",
        "payload": {
            "sid": f"scheduler__admin__search__{alert_id}",
            "search_name": search_name,
            "app": "SplunkEnterpriseSecuritySuite",
            "owner": "admin",
            "result": {
                **notable_fields,
                "severity": severity.lower(),
            },
        },
    }


# ---------------------------------------------------------------------------
# Stream a pass
# ---------------------------------------------------------------------------


def stream_pass(rows, pass_num, total_passes):
    """Stream one pass of alerts through Apollo. Returns list of results."""
    total = len(rows)
    results = []
    latencies = []

    for i, row in enumerate(rows, 1):
        alert_id = row.get("id", f"csv-{i}")
        severity = row.get("severity", "HIGH")
        customer_id = row.get("customer_id", "CUST-LSUAM")

        try:
            stix_bundle = json.loads(row["stix_bundle"])
        except (json.JSONDecodeError, KeyError) as e:
            print(f"  [{i:>3}/{total}] {RED}! CSV parse error: {e}{RESET}")
            continue

        payload = wrap_as_splunk_webhook(alert_id, stix_bundle, severity, customer_id)
        status, data, lat = webhook_send(payload)
        latencies.append(lat)

        apollo_status = data.get("status", "?")
        alert_id_out = data.get("alertId", data.get("groupedWith", "?"))

        icon = {
            "normalized": f"{GREEN}.{RESET}",
            "deduplicated": f"{RED}x{RESET}",
            "pending_review": f"{YELLOW}~{RESET}",
            "quarantined": f"{YELLOW}q{RESET}",
        }.get(apollo_status, f"{RED}!{RESET}")

        print(
            f"  [{i:>3}/{total}] {icon} {apollo_status:<16} "
            f"HTTP {status}  {DIM}{lat}ms  {alert_id[:24]}{RESET}"
        )

        results.append({
            "pass": pass_num,
            "index": i,
            "csv_alert_id": alert_id,
            "apollo_alert_id": alert_id_out,
            "http_status": status,
            "apollo_status": apollo_status,
            "latency_ms": lat,
            "response": data,
        })

        logger.info(
            "PASS %d  ALERT %d/%d  csv_id=%s  http=%d  status=%s  latency=%dms",
            pass_num, i, total, alert_id, status, apollo_status, lat,
        )

        # Small delay between alerts to avoid connection issues
        if i < total:
            time.sleep(1)

    return results, latencies


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(limit=None, no_flush=False):
    print(f"\n{BOLD}{'=' * 72}{RESET}")
    print(f"{BOLD}Apollo Dedup E2E — Production Validation (cip-command#699){RESET}")
    print(f"{BOLD}{'=' * 72}{RESET}")
    print(f"Apollo:    {CYAN}{APOLLO_URL}{RESET}")
    print(f"Gateway:   {CYAN}{GATEWAY_URL}{RESET}")
    print(f"Auth:      HMAC-SHA256 (secret length: {len(WEBHOOK_SECRET)})")
    print(f"CSV:       {CYAN}{CSV_INPUT}{RESET}")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print(f"Log file:  {LOG_FILE}")
    print(f"{'=' * 72}\n")

    logger.info("=" * 72)
    logger.info("Apollo Dedup E2E  Apollo=%s  Gateway=%s", APOLLO_URL, GATEWAY_URL)
    logger.info("=" * 72)

    if not WEBHOOK_SECRET:
        print(f"{RED}No CIP_WEBHOOK_SECRET set. Check .env{RESET}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Pre-flight: dedup engine health
    # ------------------------------------------------------------------
    print(f"{BOLD}-- Pre-flight --{RESET}")
    status, data = gateway_request("GET", "/api/dedup/health")
    if status == 200 and data.get("status") == "healthy":
        redis = data.get("redis", "?")
        print(f"  {GREEN}PASS{RESET}  Dedup engine health: {data['status']}, Redis: {redis}")
    else:
        print(f"  {RED}FAIL{RESET}  Dedup engine health: HTTP {status} — {data}")
        print(f"  {RED}Cannot proceed without a healthy dedup engine.{RESET}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # Flush dedup state
    # ------------------------------------------------------------------
    if not no_flush:
        print(f"  {YELLOW}Flushing dedup engine state...{RESET}", end=" ")
        status, data = gateway_request("DELETE", "/api/dedup/admin/flush")
        if status == 200:
            print(f"{GREEN}done{RESET}")
        else:
            print(f"{RED}HTTP {status}: {data}{RESET}")
        time.sleep(1)

    # ------------------------------------------------------------------
    # Load CSV
    # ------------------------------------------------------------------
    with open(CSV_INPUT, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if limit:
        rows = rows[:limit]
    total = len(rows)
    print(f"  Alerts to stream: {total}")

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Stream alerts — CSV contains intentional duplicates
    # ------------------------------------------------------------------
    print(f"\n{BOLD}-- Streaming alerts --{RESET}")
    start = time.time()
    pass1_results, pass1_latencies = stream_pass(rows, 1, 1)
    pass1_elapsed = time.time() - start

    pass1_accepted = ("normalized", "pending_review", "deduplicated")
    pass1_ok = sum(1 for r in pass1_results if r["apollo_status"] in pass1_accepted)
    pass1_fail = total - pass1_ok
    print(f"\n  Result: {GREEN}{pass1_ok}/{total} accepted{RESET}", end="")
    if pass1_fail:
        print(f"  {RED}{pass1_fail} unexpected{RESET}")
    else:
        print()

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print_summary(pass1_results, pass1_latencies, pass1_elapsed, total)

    # Verdict: the CSV contains intentional duplicates. At least one alert
    # should be deduplicated if the engine is working.
    dedup_count = sum(1 for r in pass1_results if r["apollo_status"] == "deduplicated")
    new_count = sum(1 for r in pass1_results if r["apollo_status"] in ("normalized", "pending_review"))

    if dedup_count > 0 and pass1_fail == 0:
        print(f"\n{GREEN}{BOLD}All E2E dedup tests passed!{RESET}")
        print(f"{GREEN}{new_count} new, {dedup_count} deduplicated by ml-service engine{RESET}\n")
    elif pass1_fail > 0:
        print(f"\n{RED}E2E test failures detected.{RESET}\n")
    else:
        print(f"\n{YELLOW}No duplicates caught — check dedup engine wiring.{RESET}\n")

    save_results(pass1_results)

    return pass1_fail == 0 and dedup_count > 0


def print_summary(results, latencies, elapsed, alerts_per_pass):
    """Print summary statistics."""
    total = len(results)
    rate = total / elapsed if elapsed > 0 else 0

    print(f"\n{'=' * 72}")
    print(f"{BOLD}SUMMARY{RESET}")
    print(f"{'=' * 72}")
    print(f"  Total alerts:  {total}")
    print(f"  Elapsed:       {elapsed:.1f}s ({rate:.1f} alerts/sec)")

    if latencies:
        s = sorted(latencies)
        print(f"  Latency:       p50={s[len(s)//2]}ms  p95={s[int(len(s)*0.95)]}ms")

    status_counts = {}
    for r in results:
        st = r["apollo_status"]
        status_counts[st] = status_counts.get(st, 0) + 1

    print(f"\n  Pipeline outcomes:")
    for st in ["normalized", "deduplicated", "pending_review", "quarantined"]:
        count = status_counts.get(st, 0)
        if count == 0:
            continue
        pct = f"{count / total * 100:.0f}%" if total > 0 else "0%"
        color = {
            "normalized": GREEN, "deduplicated": RED,
            "pending_review": YELLOW, "quarantined": YELLOW,
        }.get(st, "")
        print(f"    {color}{st:<18}{RESET}  {count:>3}  ({pct})")

    for st in status_counts:
        if st not in ("normalized", "deduplicated", "pending_review", "quarantined"):
            print(f"    {RED}{st:<18}{RESET}  {status_counts[st]:>3}")

    # Per-pass breakdown
    passes = {}
    for r in results:
        p = r.get("pass", 1)
        passes.setdefault(p, []).append(r)

    if len(passes) > 1:
        print(f"\n  Per-pass breakdown:")
        for p in sorted(passes):
            p_results = passes[p]
            p_counts = {}
            for r in p_results:
                st = r["apollo_status"]
                p_counts[st] = p_counts.get(st, 0) + 1
            summary = ", ".join(f"{st}: {c}" for st, c in sorted(p_counts.items()))
            print(f"    Pass {p}: {summary}")

    print(f"{'=' * 72}")


def save_results(results):
    """Save results to JSON."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_file = os.path.join(LOG_DIR, f"stream_dedup_e2e_{ts}.json")
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults: {results_file}")
    print(f"Full log: {LOG_FILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Apollo Dedup E2E — stream real alerts and validate dedup verdicts"
    )
    parser.add_argument("--limit", type=int, help="Max alerts to stream")
    parser.add_argument("--no-flush", action="store_true", help="Skip dedup state flush")
    args = parser.parse_args()

    success = run(limit=args.limit, no_flush=args.no_flush)
    sys.exit(0 if success else 1)
