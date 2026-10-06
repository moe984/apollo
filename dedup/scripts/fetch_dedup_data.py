"""
Fetch dedup data from Apollo's Postgres via GitHub Actions OIDC.

Same pattern as false-positive-predictor/fetch_alerts.py:
  1. Triggers extract-dedup-data.yml workflow in tekstream-cip/apollo
  2. Workflow uses GitHub OIDC → AWS STS → Secrets Manager → ECS Fargate task
  3. ECS task queries Postgres inside the VPC, outputs CSV to CloudWatch
  4. Workflow uploads CSV as artifact
  5. This script downloads the artifact locally

Output files (mode=all):
    dedup/data/dedup_input.csv       <- all alerts with stix_bundle + raw_payload
    dedup/data/dedup_decisions.csv   <- deduplicated/pending_review alerts with parent info
    dedup/data/dedup_feedback.csv    <- analyst corrections to dedup decisions
    dedup/data/fingerprints.csv      <- IOC fingerprints per alert

Prerequisites:
    - gh CLI installed and authenticated (brew install gh && gh auth login)

Usage:
    python fetch_dedup_data.py                                # all datasets
    python fetch_dedup_data.py --mode dedup_decisions          # decisions only
    python fetch_dedup_data.py --customer CUST-LSUAM --limit 500
    python fetch_dedup_data.py --run-id 12345                  # download from existing run
"""

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = "tekstream-cip/apollo"
WORKFLOW = "extract-dedup-data.yml"
WORKFLOW_REF = "main"
POLL_INTERVAL = 10
TIMEOUT = 300

# Resolve output directory relative to this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")

ARTIFACT_NAMES = [
    "dedup-input",
    "dedup-decisions",
    "dedup-feedback",
    "dedup-fingerprints",
]

FILE_MAP = {
    "dedup-input": "dedup_input.csv",
    "dedup-decisions": "dedup_decisions.csv",
    "dedup-feedback": "dedup_feedback.csv",
    "dedup-fingerprints": "fingerprints.csv",
}


def run_gh(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["gh"] + args
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if check and result.returncode != 0:
        print(f"  gh error: {result.stderr.strip()}", file=sys.stderr)
    return result


def check_gh_available():
    if not shutil.which("gh"):
        print("ERROR: gh CLI not found. Install with: brew install gh", file=sys.stderr)
        sys.exit(1)
    result = run_gh(["auth", "status"], check=False)
    if result.returncode != 0:
        print("ERROR: gh not authenticated. Run: gh auth login", file=sys.stderr)
        sys.exit(1)


def trigger_workflow(customer_id: str, mode: str, limit: str, ref: str = WORKFLOW_REF):
    result = run_gh([
        "workflow", "run", WORKFLOW,
        "--repo", REPO,
        "--ref", ref,
        "-f", f"customer_id={customer_id}",
        "-f", f"mode={mode}",
        "-f", f"limit={limit}",
    ])
    if result.returncode != 0:
        print(f"ERROR: Failed to trigger workflow: {result.stderr}", file=sys.stderr)
        sys.exit(1)


def find_recent_run(limit: int = 3) -> dict | None:
    result = run_gh([
        "run", "list", "--repo", REPO, "--workflow", WORKFLOW,
        "--limit", str(limit), "--json", "databaseId,status,conclusion,createdAt",
    ])
    if result.returncode != 0:
        return None
    runs = json.loads(result.stdout)
    return runs[0] if runs else None


def wait_for_run(run_id: int) -> str:
    start = time.time()
    while (time.time() - start) < TIMEOUT:
        r = run_gh(["run", "view", str(run_id), "--repo", REPO, "--json", "status,conclusion"], check=False)
        if r.returncode != 0:
            time.sleep(POLL_INTERVAL)
            continue
        data = json.loads(r.stdout)
        if data.get("status") == "completed":
            return data.get("conclusion", "")
        elapsed = int(time.time() - start)
        print(f"  [{elapsed}s] status={data.get('status')}...")
        time.sleep(POLL_INTERVAL)
    return "timeout"


def download_artifact(run_id: int, artifact_name: str, customer_id: str, output_path: str) -> bool:
    full_name = f"{artifact_name}-{customer_id}"
    with tempfile.TemporaryDirectory() as tmpdir:
        dl = run_gh([
            "run", "download", str(run_id), "--repo", REPO,
            "--name", full_name, "--dir", tmpdir,
        ], check=False)
        if dl.returncode != 0:
            return False

        # Find the CSV in the download directory
        csv_name = FILE_MAP.get(artifact_name, f"{artifact_name}.csv")
        src = os.path.join(tmpdir, csv_name)
        if not os.path.exists(src):
            # Try finding any CSV
            for f in os.listdir(tmpdir):
                if f.endswith(".csv"):
                    src = os.path.join(tmpdir, f)
                    break
            else:
                return False

        shutil.copy2(src, output_path)
        return True


def print_csv_summary(path: str):
    if not os.path.exists(path):
        return
    with open(path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if not rows:
        print(f"  {os.path.basename(path)}: EMPTY")
        return

    size_kb = os.path.getsize(path) / 1024
    columns = list(rows[0].keys())
    print(f"  {os.path.basename(path)}: {len(rows)} rows, {len(columns)} cols, {size_kb:.0f} KB")

    # Status distribution
    if "status" in columns:
        statuses: dict[str, int] = {}
        for r in rows:
            s = r.get("status") or "unknown"
            statuses[s] = statuses.get(s, 0) + 1
        print(f"    Status: {dict(sorted(statuses.items(), key=lambda x: -x[1]))}")

    # Source distribution
    if "source" in columns:
        sources: dict[str, int] = {}
        for r in rows:
            s = r.get("source") or "unknown"
            sources[s] = sources.get(s, 0) + 1
        print(f"    Source: {dict(sorted(sources.items(), key=lambda x: -x[1]))}")

    # Severity distribution
    if "severity" in columns:
        sevs: dict[str, int] = {}
        for r in rows:
            s = r.get("severity") or "unknown"
            sevs[s] = sevs.get(s, 0) + 1
        print(f"    Severity: {dict(sorted(sevs.items(), key=lambda x: -x[1]))}")

    # Dedup method distribution (for decisions)
    if "dedup_metadata" in columns:
        methods: dict[str, int] = {}
        for r in rows:
            meta = r.get("dedup_metadata", "")
            if meta:
                try:
                    parsed = json.loads(meta)
                    m = parsed.get("dedup_method", "unknown")
                except (json.JSONDecodeError, TypeError):
                    m = "parse_error"
            else:
                m = "none"
            methods[m] = methods.get(m, 0) + 1
        print(f"    Dedup method: {dict(sorted(methods.items(), key=lambda x: -x[1]))}")

    # Feedback disposition distribution
    if "corrected_disposition" in columns:
        disps: dict[str, int] = {}
        for r in rows:
            d = r.get("corrected_disposition") or "none"
            disps[d] = disps.get(d, 0) + 1
        print(f"    Disposition: {dict(sorted(disps.items(), key=lambda x: -x[1]))}")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch dedup data from Apollo via GitHub Actions OIDC",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--customer", default="CUST-LSUAM",
                        help="Customer ID (default: CUST-LSUAM)")
    parser.add_argument("--mode", choices=["all", "dedup_decisions", "dedup_input", "dedup_feedback"],
                        default="all", help="Which dataset to pull (default: all)")
    parser.add_argument("--limit", default="2000",
                        help="Max rows per query (default: 2000)")
    parser.add_argument("--timeout", type=int, default=300,
                        help="Max seconds to wait (default: 300)")
    parser.add_argument("--run-id", type=int, default=None,
                        help="Download from existing run instead of triggering new one")
    parser.add_argument("--ref", default=WORKFLOW_REF,
                        help=f"Git ref for workflow (default: {WORKFLOW_REF})")
    args = parser.parse_args()

    global TIMEOUT
    TIMEOUT = args.timeout
    workflow_ref = args.ref

    check_gh_available()
    os.makedirs(DATA_DIR, exist_ok=True)

    if args.run_id:
        run_id = args.run_id
        print(f"Using existing run {run_id}")
    else:
        print(f"[1/3] Triggering workflow (customer={args.customer}, mode={args.mode}, limit={args.limit})...")
        trigger_workflow(args.customer, args.mode, args.limit, ref=workflow_ref)
        print("  Waiting for GitHub to register the run...")
        time.sleep(5)

        run = find_recent_run()
        if not run:
            print("ERROR: Could not find the triggered run.", file=sys.stderr)
            sys.exit(1)
        run_id = run["databaseId"]
        print(f"  Run ID: {run_id}")

    print(f"[2/3] Waiting for run {run_id}...")
    conclusion = wait_for_run(run_id)
    if conclusion != "success":
        print(f"\n  Run failed: {conclusion}", file=sys.stderr)
        print(f"  View: gh run view {run_id} --repo {REPO} --log", file=sys.stderr)
        sys.exit(1)

    print(f"[3/3] Downloading artifacts...")
    downloaded = []
    for artifact in ARTIFACT_NAMES:
        output_file = os.path.join(DATA_DIR, FILE_MAP[artifact])
        if download_artifact(run_id, artifact, args.customer, output_file):
            downloaded.append(output_file)
            print(f"  Downloaded: {output_file}")
        else:
            print(f"  Skipped: {artifact} (no data or not in this mode)")

    if not downloaded:
        print("ERROR: No artifacts downloaded.", file=sys.stderr)
        sys.exit(1)

    print(f"\n--- Summary ---")
    for path in downloaded:
        print_csv_summary(path)

    print(f"\nFiles saved to: {DATA_DIR}")
    print("Done.")


if __name__ == "__main__":
    main()
