#!/usr/bin/env python3
"""
Extract STIX Bundles from Apollo

Extracts the STIX 2.1 bundles that Apollo sends to the dedup engine.
Each bundle is saved as an individual JSON file for manual verification
and cross-validation against the ML-service dedup engine.

Usage:
    # Extract via GitHub Actions (OIDC, no local creds needed)
    python extract.py --github

    # Extract from local/tunneled database
    python extract.py --database-url postgresql://...

    # Limit and filter
    python extract.py --github --limit 50 --status normalized
    python extract.py --github --status deduplicated

    # Include raw payload and dedup metadata for full context
    python extract.py --github --full
"""
import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# =========================================================================
# Logging
# =========================================================================

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

log_filename = datetime.now(timezone.utc).strftime("extract_%Y%m%d_%H%M%S.log")
log_filepath = LOG_DIR / log_filename


class DualHandler(logging.Handler):
    def __init__(self, filepath):
        super().__init__()
        self.console = logging.StreamHandler(sys.stdout)
        self.console.setFormatter(logging.Formatter("%(message)s"))
        self.file = logging.FileHandler(filepath)
        self.file.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        )

    def emit(self, record):
        self.console.emit(record)
        self.file.emit(record)


log = logging.getLogger("extract")
log.setLevel(logging.INFO)
log.addHandler(DualHandler(log_filepath))

REPO = "tekstream-cip/apollo"


# =========================================================================
# STIX bundle IOC parser (mirrors ML-service stix_parser.py)
# =========================================================================

def parse_iocs_from_stix(bundle):
    """Extract IOCs from a STIX 2.1 bundle, mirroring the ML-service parser."""
    iocs = {
        "ips": [],
        "domains": [],
        "urls": [],
        "file_hashes": [],
        "usernames": [],
        "alert_name": None,
    }
    objects = bundle.get("objects", [])
    for obj in objects:
        obj_type = obj.get("type", "")
        if obj_type == "ipv4-addr":
            val = obj.get("value")
            if val:
                iocs["ips"].append(val)
        elif obj_type == "ipv6-addr":
            val = obj.get("value")
            if val:
                iocs["ips"].append(val)
        elif obj_type == "domain-name":
            val = obj.get("value")
            if val:
                iocs["domains"].append(val)
        elif obj_type == "url":
            val = obj.get("value")
            if val:
                iocs["urls"].append(val)
        elif obj_type == "file":
            hashes = obj.get("hashes", {})
            for alg in ["SHA-256", "SHA-1", "MD5"]:
                if alg in hashes:
                    iocs["file_hashes"].append(hashes[alg])
                    break
        elif obj_type == "user-account":
            uid = obj.get("user_id") or obj.get("account_login")
            if uid:
                iocs["usernames"].append(uid)
        elif obj_type == "indicator":
            name = obj.get("name")
            if name:
                iocs["alert_name"] = name

    return iocs


# =========================================================================
# GitHub Actions mode
# =========================================================================

def extract_via_github(args):
    """Run extraction as an ECS task via GitHub Actions."""
    try:
        subprocess.run(["gh", "--version"], capture_output=True, check=True)
    except FileNotFoundError:
        log.error("GitHub CLI (gh) not found. Install it: https://cli.github.com")
        sys.exit(1)

    # Build the Node.js query script
    status_filter = f"AND a.status = '{args.status}'" if args.status else ""
    limit = args.limit

    log.info("")
    log.info("=" * 50)
    log.info("  Extract STIX Bundles (GitHub Actions)")
    log.info("=" * 50)
    log.info(f"  Limit:   {limit}")
    log.info(f"  Status:  {args.status or 'all'}")
    log.info(f"  Full:    {args.full}")
    log.info(f"  Log:     {log_filepath}")
    log.info("")

    # Build the query
    if args.full:
        select = "a.id, a.source, a.severity, a.status, a.stix_bundle, a.raw_payload, a.dedup_metadata, a.dedup_count, a.parent_alert_id, a.created_at"
    else:
        select = "a.id, a.source, a.severity, a.status, a.stix_bundle, a.created_at"

    query = f"SELECT {select} FROM alerts a WHERE a.customer_id = 'CUST-LSUAM' {status_filter} ORDER BY a.created_at DESC LIMIT {limit}"

    # Write the Node.js script to a temp file, build ECS overrides
    js_script = f"""
const {{ Pool }} = require('pg');
const p = new Pool({{ connectionString: process.env.DATABASE_URL, ssl: {{ rejectUnauthorized: false }} }});
(async () => {{
  const r = await p.query(`{query}`);
  console.log(JSON.stringify(r.rows));
  await p.end();
}})().catch(e => {{ console.error(e); process.exit(1); }});
"""

    log.info("Triggering ECS task to extract data...")

    # Use the purge workflow pattern: OIDC + ECS task override
    result = subprocess.run(
        ["gh", "api", "--method", "POST",
         f"repos/{REPO}/actions/workflows/purge-data.yml/dispatches",
         "-f", "ref=main",
         "-f", "inputs[dry_run]=true"],
        capture_output=True, text=True,
    )

    # Actually, let's use the extract-dedup-data workflow that already exists
    log.info("Using extract-dedup-data workflow...")
    result = subprocess.run(
        ["gh", "workflow", "run", "extract-dedup-data.yml",
         "--repo", REPO, "--ref", "main",
         "-f", f"customer_id=CUST-LSUAM",
         "-f", f"mode=dedup_input",
         "-f", f"limit={limit}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log.error(f"Failed to trigger workflow: {result.stderr}")
        sys.exit(1)

    log.info("Waiting for run to start...")
    time.sleep(3)

    # Find the run
    result = subprocess.run(
        ["gh", "run", "list", "--repo", REPO,
         "--workflow", "extract-dedup-data.yml",
         "--limit", "1", "--json", "databaseId",
         "--jq", ".[0].databaseId"],
        capture_output=True, text=True, check=True,
    )
    run_id = result.stdout.strip()
    log.info(f"  Run ID: {run_id}")
    log.info(f"  URL: https://github.com/{REPO}/actions/runs/{run_id}")
    log.info("")

    log.info("Watching run...")
    watch = subprocess.run(
        ["gh", "run", "watch", run_id, "--repo", REPO, "--exit-status"],
        timeout=300,
    )
    if watch.returncode != 0:
        log.error(f"Workflow failed. Check: https://github.com/{REPO}/actions/runs/{run_id}")
        sys.exit(1)

    log.info("")
    log.info("Downloading artifacts...")
    artifact_dir = OUTPUT_DIR / f"run_{run_id}"
    artifact_dir.mkdir(exist_ok=True)
    subprocess.run(
        ["gh", "run", "download", run_id, "--repo", REPO, "--dir", str(artifact_dir)],
        check=True,
    )

    # Parse the downloaded CSVs and extract STIX bundles
    log.info(f"Artifacts saved to: {artifact_dir}")
    process_artifacts(artifact_dir, args)


def process_artifacts(artifact_dir, args):
    """Parse extracted data and save individual STIX bundles."""
    import csv

    bundles_dir = OUTPUT_DIR / "stix_bundles"
    bundles_dir.mkdir(exist_ok=True)

    csv_files = list(artifact_dir.rglob("*.csv"))
    if not csv_files:
        log.warning("No CSV files found in artifacts.")
        return

    bundle_count = 0
    empty_count = 0
    ioc_summary = {"ips": 0, "domains": 0, "urls": 0, "file_hashes": 0, "usernames": 0}

    for csv_file in csv_files:
        if "dedup_input" not in csv_file.name:
            continue
        log.info(f"Processing: {csv_file.name}")

        with open(csv_file, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                alert_id = row.get("id", "unknown")
                stix_raw = row.get("stix_bundle", "")

                if not stix_raw or stix_raw == "null":
                    empty_count += 1
                    continue

                try:
                    bundle = json.loads(stix_raw)
                except json.JSONDecodeError:
                    log.warning(f"  Invalid JSON in stix_bundle for alert {alert_id}")
                    continue

                # Parse IOCs
                iocs = parse_iocs_from_stix(bundle)

                # Save bundle with metadata
                output = {
                    "alert_id": alert_id,
                    "source": row.get("source", ""),
                    "severity": row.get("severity", ""),
                    "status": row.get("status", ""),
                    "created_at": row.get("created_at", ""),
                    "stix_bundle": bundle,
                    "extracted_iocs": iocs,
                    "ioc_counts": {k: len(v) if isinstance(v, list) else (1 if v else 0) for k, v in iocs.items()},
                }

                if args.full:
                    output["dedup_metadata"] = json.loads(row.get("dedup_metadata", "null") or "null")
                    output["dedup_count"] = row.get("dedup_count", 0)
                    output["parent_alert_id"] = row.get("parent_alert_id", "")

                bundle_file = bundles_dir / f"{alert_id}.json"
                with open(bundle_file, 'w') as bf:
                    json.dump(output, bf, indent=2)

                bundle_count += 1
                for k in ioc_summary:
                    if isinstance(iocs.get(k), list):
                        ioc_summary[k] += len(iocs[k])

    log.info("")
    log.info("=" * 50)
    log.info("  Extraction Complete")
    log.info("=" * 50)
    log.info(f"  Bundles saved:   {bundle_count}")
    log.info(f"  Empty bundles:   {empty_count}")
    log.info(f"  Output dir:      {bundles_dir}")
    log.info("")
    log.info("--- IOC totals across all bundles ---")
    for ioc_type, count in ioc_summary.items():
        log.info(f"  {ioc_type:<20} {count}")

    if empty_count > 0:
        log.warning(f"\n  {empty_count} alerts had empty STIX bundles (no IOCs for dedup)")

    log.info(f"\nLog saved to: {log_filepath}")


# =========================================================================
# Direct database mode
# =========================================================================

def extract_direct(args):
    """Extract STIX bundles directly from the database."""
    try:
        import psycopg2
    except ImportError:
        log.error("psycopg2 not installed. Run: pip install psycopg2-binary")
        sys.exit(1)

    if args.database_url:
        db_url = args.database_url
    elif args.from_aws:
        log.info("Fetching DATABASE_URL from AWS Secrets Manager...")
        result = subprocess.run(
            ["aws", "ecs", "describe-task-definition",
             "--task-definition", "apollo", "--region", "us-east-1",
             "--query", "taskDefinition.containerDefinitions[0].secrets[?name=='DATABASE_URL'].valueFrom",
             "--output", "text"],
            capture_output=True, text=True, check=True,
        )
        secret_arn = result.stdout.strip()
        result = subprocess.run(
            ["aws", "secretsmanager", "get-secret-value",
             "--secret-id", secret_arn, "--region", "us-east-1",
             "--query", "SecretString", "--output", "text"],
            capture_output=True, text=True, check=True,
        )
        db_url = result.stdout.strip()
    else:
        db_url = os.environ.get("DATABASE_URL")

    if not db_url:
        log.error("No database URL. Use --database-url, --from-aws, or --github.")
        sys.exit(1)

    masked = db_url.split("@")[-1] if "@" in db_url else db_url

    status_filter = f"AND a.status = '{args.status}'" if args.status else ""

    if args.full:
        select = "a.id, a.source, a.severity, a.status, a.stix_bundle, a.raw_payload, a.dedup_metadata, a.dedup_count, a.parent_alert_id, a.created_at"
    else:
        select = "a.id, a.source, a.severity, a.status, a.stix_bundle, a.created_at"

    log.info("")
    log.info("=" * 50)
    log.info("  Extract STIX Bundles")
    log.info("=" * 50)
    log.info(f"  Target:  ...@{masked}")
    log.info(f"  Limit:   {args.limit}")
    log.info(f"  Status:  {args.status or 'all'}")
    log.info(f"  Full:    {args.full}")
    log.info(f"  Log:     {log_filepath}")
    log.info("")

    try:
        conn = psycopg2.connect(db_url, sslmode="require")
    except Exception:
        try:
            conn = psycopg2.connect(db_url)
        except Exception as e:
            log.error(f"Cannot connect: {e}")
            sys.exit(1)

    query = f"SELECT {select} FROM alerts a WHERE a.customer_id = 'CUST-LSUAM' {status_filter} ORDER BY a.created_at DESC LIMIT %s"

    with conn.cursor() as cur:
        cur.execute(query, (args.limit,))
        columns = [desc[0] for desc in cur.description]
        rows = cur.fetchall()

    conn.close()

    log.info(f"Fetched {len(rows)} alerts from database.")

    bundles_dir = OUTPUT_DIR / "stix_bundles"
    bundles_dir.mkdir(exist_ok=True)

    bundle_count = 0
    empty_count = 0
    ioc_summary = {"ips": 0, "domains": 0, "urls": 0, "file_hashes": 0, "usernames": 0}

    for row in rows:
        data = dict(zip(columns, row))
        alert_id = data["id"]
        bundle = data.get("stix_bundle")

        if not bundle:
            empty_count += 1
            continue

        if isinstance(bundle, str):
            bundle = json.loads(bundle)

        iocs = parse_iocs_from_stix(bundle)

        output = {
            "alert_id": alert_id,
            "source": data.get("source", ""),
            "severity": data.get("severity", ""),
            "status": data.get("status", ""),
            "created_at": str(data.get("created_at", "")),
            "stix_bundle": bundle,
            "extracted_iocs": iocs,
            "ioc_counts": {k: len(v) if isinstance(v, list) else (1 if v else 0) for k, v in iocs.items()},
        }

        if args.full:
            dedup_meta = data.get("dedup_metadata")
            if isinstance(dedup_meta, str):
                dedup_meta = json.loads(dedup_meta)
            output["dedup_metadata"] = dedup_meta
            output["dedup_count"] = data.get("dedup_count", 0)
            output["parent_alert_id"] = data.get("parent_alert_id", "")

        bundle_file = bundles_dir / f"{alert_id}.json"
        with open(bundle_file, 'w') as f:
            json.dump(output, f, indent=2, default=str)

        bundle_count += 1
        for k in ioc_summary:
            if isinstance(iocs.get(k), list):
                ioc_summary[k] += len(iocs[k])

    log.info("")
    log.info("=" * 50)
    log.info("  Extraction Complete")
    log.info("=" * 50)
    log.info(f"  Bundles saved:   {bundle_count}")
    log.info(f"  Empty bundles:   {empty_count}")
    log.info(f"  Output dir:      {bundles_dir}")
    log.info("")
    log.info("--- IOC totals across all bundles ---")
    for ioc_type, count in ioc_summary.items():
        log.info(f"  {ioc_type:<20} {count}")

    if empty_count > 0:
        log.warning(f"\n  {empty_count} alerts had empty STIX bundles (no IOCs for dedup)")

    log.info(f"\nLog saved to: {log_filepath}")


# =========================================================================
# Main
# =========================================================================

def main():
    parser = argparse.ArgumentParser(description="Extract STIX bundles from Apollo for dedup cross-validation")
    parser.add_argument("--github", action="store_true", help="Extract via GitHub Actions (OIDC)")
    parser.add_argument("--database-url", help="PostgreSQL connection string")
    parser.add_argument("--from-aws", action="store_true", help="Fetch DATABASE_URL from AWS Secrets Manager")
    parser.add_argument("--limit", type=int, default=100, help="Max alerts to extract (default: 100)")
    parser.add_argument("--status", choices=["normalized", "deduplicated", "pending_review", "ingested"],
                        help="Filter by alert status")
    parser.add_argument("--full", action="store_true",
                        help="Include raw_payload, dedup_metadata, parent_alert_id")
    args = parser.parse_args()

    if args.github:
        extract_via_github(args)
    else:
        extract_direct(args)


if __name__ == "__main__":
    main()
