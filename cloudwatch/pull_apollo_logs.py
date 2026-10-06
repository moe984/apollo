#!/usr/bin/env python3
"""
Pull Apollo CloudWatch logs from /ecs/apollo.

Designed to run inside a GitHub Actions workflow with OIDC-based AWS
credentials. The workflow (debug-apollo-logs.yml in tekstream-cip/cip)
handles authentication via aws-actions/configure-aws-credentials@v4.

Primary use case: verify dedup client initialization and ml-service
call logs after deployment. Created as follow-up to cip-command#699
and cip-command#703.

Usage (inside workflow):
    python3 pull_apollo_logs.py --minutes 120 --filter "dedup|initialized"

Usage (local, requires AWS creds in env):
    export AWS_PROFILE=your-profile
    python3 pull_apollo_logs.py --minutes 60
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import boto3


LOG_GROUP = "/ecs/apollo"
REGION = "us-east-1"
DEFAULT_FILTER = "dedup|Dedup|ml-service|gateway|circuit|initialized"

# Dedup verification markers from Apollo source code:
#   - Init: apollo/src/index.ts (createDedupServiceClient)
#   - Calls: apollo/src/ingestion/pipeline.ts (step 5)
#   - Client: apollo/src/ingestion/dedup/dedup-service-client.ts
DEDUP_MARKERS = {
    "init_success": "Dedup service client initialized",
    "init_skip": "DEDUP_SERVICE_URL not set",
    "via_gateway": "viaGateway",
    "call_start": "Calling ml-service",
    "verdict": "Verdict:",
    "fail_open": "fail-open",
    "circuit_open": "circuit breaker",
    "unavailable": "Dedup service unavailable",
}


def pull_logs(minutes, filter_pattern, limit):
    client = boto3.client("logs", region_name=REGION)

    end_time = int(time.time() * 1000)
    start_time = end_time - (minutes * 60 * 1000)

    start_dt = datetime.fromtimestamp(start_time / 1000, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(end_time / 1000, tz=timezone.utc)

    print(f"Log group:  {LOG_GROUP}")
    print(f"Region:     {REGION}")
    print(f"Time range: {start_dt:%Y-%m-%d %H:%M:%S UTC} to {end_dt:%Y-%m-%d %H:%M:%S UTC}")
    print(f"Filter:     {filter_pattern or '(none)'}")
    print(f"Limit:      {limit}")
    print("---")

    all_events = []
    next_token = None

    while True:
        kwargs = {
            "logGroupName": LOG_GROUP,
            "startTime": start_time,
            "endTime": end_time,
            "limit": min(limit - len(all_events), 100),
            "interleaved": True,
        }
        if next_token:
            kwargs["nextToken"] = next_token

        try:
            response = client.filter_log_events(**kwargs)
        except client.exceptions.ResourceNotFoundException:
            print(f"ERROR: Log group '{LOG_GROUP}' not found in {REGION}")
            sys.exit(1)

        events = response.get("events", [])
        all_events.extend(events)

        next_token = response.get("nextToken")
        if not next_token or len(all_events) >= limit:
            break

    print(f"Total events fetched: {len(all_events)}")

    if filter_pattern:
        pattern = re.compile(filter_pattern, re.IGNORECASE)
        filtered = [e for e in all_events if pattern.search(e.get("message", ""))]
        print(f"Events matching filter: {len(filtered)}")
    else:
        filtered = all_events

    print("---\n")
    return filtered


def format_events(events):
    lines = []
    for event in events:
        ts = datetime.fromtimestamp(event["timestamp"] / 1000, tz=timezone.utc)
        msg = event.get("message", "").strip()
        stream = event.get("logStreamName", "unknown")
        lines.append(f"[{ts:%Y-%m-%d %H:%M:%S}] [{stream}] {msg}")
    return lines


def print_dedup_summary(events):
    print("\n--- Dedup Verification Summary ---")
    found_any = False
    for label, marker in DEDUP_MARKERS.items():
        matches = [e for e in events if marker.lower() in e.get("message", "").lower()]
        if matches:
            found_any = True
            ts = datetime.fromtimestamp(
                matches[-1]["timestamp"] / 1000, tz=timezone.utc
            )
            print(
                f"  {label}: {len(matches)} occurrence(s), "
                f"last at {ts:%Y-%m-%d %H:%M:%S UTC}"
            )
    if not found_any:
        print("  No dedup-related markers found in the filtered logs.")
        print("  Try increasing --minutes or broadening the filter.")


def write_github_summary(events, lines):
    """Write results to GitHub Actions step summary if running in CI."""
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return

    with open(summary_path, "a") as f:
        f.write("## Apollo CloudWatch Logs\n\n")
        f.write(f"**Total events:** {len(events)}\n\n")

        # Dedup summary table
        f.write("### Dedup Verification\n\n")
        f.write("| Marker | Count | Last Seen |\n")
        f.write("|--------|-------|-----------|\n")
        for label, marker in DEDUP_MARKERS.items():
            matches = [
                e for e in events if marker.lower() in e.get("message", "").lower()
            ]
            if matches:
                ts = datetime.fromtimestamp(
                    matches[-1]["timestamp"] / 1000, tz=timezone.utc
                )
                f.write(f"| {label} | {len(matches)} | {ts:%Y-%m-%d %H:%M:%S UTC} |\n")
            else:
                f.write(f"| {label} | 0 | - |\n")

        # Log output
        f.write("\n### Log Output\n\n")
        if lines:
            f.write("```\n")
            for line in lines[:200]:
                f.write(line + "\n")
            if len(lines) > 200:
                f.write(f"\n... ({len(lines) - 200} more lines truncated)\n")
            f.write("```\n")
        else:
            f.write("No matching log events found.\n")


def main():
    parser = argparse.ArgumentParser(
        description="Pull Apollo CloudWatch logs for dedup verification"
    )
    parser.add_argument(
        "--minutes",
        type=int,
        default=60,
        help="Minutes of logs to pull (default: 60)",
    )
    parser.add_argument(
        "--filter",
        type=str,
        default=DEFAULT_FILTER,
        help=f"Regex filter pattern (default: '{DEFAULT_FILTER}')",
    )
    parser.add_argument(
        "--no-filter",
        action="store_true",
        help="Disable filtering, pull all logs",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Max events to fetch (default: 500)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Save output to file",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Also output raw events as JSON",
    )

    args = parser.parse_args()
    filter_pattern = None if args.no_filter else args.filter

    events = pull_logs(args.minutes, filter_pattern, args.limit)
    lines = format_events(events)

    # Print to stdout
    if lines:
        print("\n".join(lines))
    else:
        print("No matching log events found.")

    # Dedup summary
    print_dedup_summary(events)

    # Save to file
    if args.output:
        with open(args.output, "w") as f:
            f.write("\n".join(lines))
        print(f"\nSaved {len(lines)} events to {args.output}")

    # JSON export
    if args.json:
        json_path = args.output.replace(".txt", ".json") if args.output else "apollo_logs.json"
        with open(json_path, "w") as f:
            json.dump(
                [
                    {
                        "timestamp": e["timestamp"],
                        "datetime": datetime.fromtimestamp(
                            e["timestamp"] / 1000, tz=timezone.utc
                        ).isoformat(),
                        "message": e.get("message", ""),
                        "stream": e.get("logStreamName", ""),
                    }
                    for e in events
                ],
                f,
                indent=2,
            )
        print(f"JSON saved to {json_path}")

    # GitHub Actions summary
    write_github_summary(events, lines)


if __name__ == "__main__":
    main()
