"""
Fetch Apollo alerts data for ML training via GitHub Actions.

The Apollo database is in a private VPC — no direct access from laptops.
This script triggers the extract-training-data.yml workflow (which runs an
ECS Fargate task inside the VPC via GitHub OIDC), waits for it to finish,
and downloads the CSV artifact locally.

Auto mode (default) triggers labeled + unlabeled in parallel, saves both
individually, then merges them into a single file for downstream use.

Output files (auto mode):
    cust_lsuam_labeled.csv              <- labeled data (if available)
    cust_lsuam_unlabeled.csv            <- unlabeled data
    cust_lsuam_splunk_alert_data.csv    <- merged (used by preprocess.py & dashboard.py)

Prerequisites:
    - gh CLI installed and authenticated (brew install gh && gh auth login)

Usage:
    python fetch_alerts.py                                      # auto mode (default)
    python fetch_alerts.py --mode unlabeled                     # raw alerts only
    python fetch_alerts.py --mode labeled                       # analyst feedback only
    python fetch_alerts.py --customer CUST-LSUAM                # specific customer
    python fetch_alerts.py --run-id 12345                       # download from existing run
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
from datetime import datetime, timezone

REPO = "tekstream-cip/apollo"
WORKFLOW = "extract-training-data.yml"
WORKFLOW_REF = "fix/feedback-panel-inline-170"  # branch with DB column names query
POLL_INTERVAL = 10
TIMEOUT = 300


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


def trigger_workflow(customer_id: str, mode: str, min_samples: str):
    run_gh([
        "workflow", "run", WORKFLOW,
        "--repo", REPO,
        "--ref", WORKFLOW_REF,
        "-f", f"customer_id={customer_id}",
        "-f", f"mode={mode}",
        "-f", f"min_samples={min_samples}",
    ])


def find_recent_runs(limit: int = 5) -> list[dict]:
    result = run_gh([
        "run", "list", "--repo", REPO, "--workflow", WORKFLOW,
        "--limit", str(limit), "--json", "databaseId,status,conclusion,createdAt",
    ])
    if result.returncode != 0:
        return []
    return json.loads(result.stdout)


def wait_for_runs(run_ids: list[int]) -> dict[int, str]:
    """Wait for multiple runs to complete. Returns {run_id: conclusion}."""
    start = time.time()
    results = {}
    pending = set(run_ids)

    while pending and (time.time() - start) < TIMEOUT:
        for rid in list(pending):
            r = run_gh(["run", "view", str(rid), "--repo", REPO, "--json", "status,conclusion"], check=False)
            if r.returncode != 0:
                continue
            data = json.loads(r.stdout)
            if data.get("status") == "completed":
                results[rid] = data.get("conclusion", "")
                pending.remove(rid)
        if pending:
            elapsed = int(time.time() - start)
            print(f"  [{elapsed}s] {len(results)}/{len(run_ids)} complete...")
            time.sleep(POLL_INTERVAL)

    for rid in pending:
        results[rid] = "timeout"
    return results


def try_download_artifact(run_id: int, customer_id: str, output_path: str) -> bool:
    """Try to download the artifact. Returns True if successful and has data rows."""
    artifact_name = f"fp-training-data-{customer_id}"
    with tempfile.TemporaryDirectory() as tmpdir:
        dl = run_gh([
            "run", "download", str(run_id), "--repo", REPO,
            "--name", artifact_name, "--dir", tmpdir,
        ], check=False)
        if dl.returncode != 0:
            return False
        src = os.path.join(tmpdir, "training_data.csv")
        if not os.path.exists(src):
            return False
        # Check it has actual data rows
        with open(src) as f:
            reader = csv.reader(f)
            next(reader, None)  # header
            if not next(reader, None):
                return False
        shutil.copy2(src, output_path)
        return True


def merge_csvs(unlabeled_path: str, labeled_path: str | None, output_path: str):
    """Merge unlabeled and labeled CSVs by left-joining on detection_id."""
    # Load unlabeled as base
    with open(unlabeled_path) as f:
        reader = csv.DictReader(f)
        unlabeled_rows = list(reader)
        base_cols = list(reader.fieldnames)

    if not labeled_path or not os.path.exists(labeled_path):
        # No labeled data — just copy unlabeled
        shutil.copy2(unlabeled_path, output_path)
        return len(unlabeled_rows), 0

    # Load labeled
    with open(labeled_path) as f:
        reader = csv.DictReader(f)
        labeled_rows = list(reader)
        labeled_cols = list(reader.fieldnames)

    # Build lookup by detection_id
    labeled_lookup = {}
    for row in labeled_rows:
        labeled_lookup[row["detection_id"]] = row

    # Extra columns from labeled that aren't in unlabeled
    extra_cols = [c for c in labeled_cols if c not in base_cols]
    all_cols = base_cols + extra_cols

    # Merge
    merged = []
    labeled_count = 0
    for row in unlabeled_rows:
        out = {c: row.get(c, "") for c in base_cols}
        labeled_match = labeled_lookup.get(row.get("detection_id"))
        if labeled_match:
            for c in extra_cols:
                out[c] = labeled_match.get(c, "")
            labeled_count += 1
        else:
            for c in extra_cols:
                out[c] = ""
        merged.append(out)

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=all_cols)
        writer.writeheader()
        writer.writerows(merged)

    return len(merged), labeled_count


def print_summary(csv_path: str):
    print(f"\n  Summary of {csv_path}")
    with open(csv_path, "r") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("  NO DATA in file.")
        return

    columns = list(rows[0].keys())
    size_mb = os.path.getsize(csv_path) / (1024 * 1024)
    print(f"  Rows: {len(rows)}, Columns: {len(columns)}, Size: {size_mb:.1f} MB")

    if "raw_severity" in columns:
        sevs: dict[str, int] = {}
        for r in rows:
            s = r.get("raw_severity") or "unknown"
            sevs[s] = sevs.get(s, 0) + 1
        print(f"  Severity: {dict(sorted(sevs.items(), key=lambda x: -x[1]))}")

    if "status" in columns:
        statuses: dict[str, int] = {}
        for r in rows:
            s = r.get("status") or "unknown"
            statuses[s] = statuses.get(s, 0) + 1
        print(f"  Status: {dict(sorted(statuses.items(), key=lambda x: -x[1]))}")

    if "label_fp" in columns:
        fps: dict[str, int] = {}
        for r in rows:
            v = r.get("label_fp", "")
            fps[v if v else "unlabeled"] = fps.get(v if v else "unlabeled", 0) + 1
        print(f"  Labels: {fps}")

    print()


def run_auto_mode(customer_id: str, min_samples: str):
    safe_cust = customer_id.lower().replace("-", "_")
    unlabeled_path = f"{safe_cust}_unlabeled.csv"
    labeled_path = f"{safe_cust}_labeled.csv"
    merged_path = f"{safe_cust}_splunk_alert_data.csv"

    # Trigger both in parallel
    print("[auto] Triggering labeled + unlabeled in parallel...")
    trigger_workflow(customer_id, "labeled", min_samples)
    trigger_workflow(customer_id, "unlabeled", min_samples)
    print("  Waiting for GitHub to register runs...")
    time.sleep(5)

    # Find the 2 runs we just triggered
    recent = find_recent_runs(limit=4)
    if len(recent) < 2:
        print("ERROR: Could not find triggered runs.", file=sys.stderr)
        sys.exit(1)
    run_ids = [r["databaseId"] for r in recent[:2]]
    print(f"  Run IDs: {run_ids}")

    # Wait for both
    print("[auto] Waiting for runs to complete...")
    conclusions = wait_for_runs(run_ids)

    # Try downloading from each successful run
    got_unlabeled = False
    got_labeled = False

    print("[auto] Downloading results...")
    for rid in run_ids:
        if conclusions.get(rid) != "success":
            continue
        # Try as unlabeled first (always try both since we don't know which run is which)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_csv = os.path.join(tmpdir, "check.csv")
            if not try_download_artifact(rid, customer_id, tmp_csv):
                continue
            # Detect mode from columns
            with open(tmp_csv) as f:
                header = csv.DictReader(f).fieldnames
            if "label_fp" in header:
                shutil.copy2(tmp_csv, labeled_path)
                got_labeled = True
                print(f"  Labeled data saved to {labeled_path}")
            else:
                shutil.copy2(tmp_csv, unlabeled_path)
                got_unlabeled = True
                print(f"  Unlabeled data saved to {unlabeled_path}")

    if not got_unlabeled:
        print("ERROR: Could not fetch unlabeled data.", file=sys.stderr)
        sys.exit(1)

    # Merge
    print("[auto] Merging into single file...")
    total, labeled_count = merge_csvs(
        unlabeled_path,
        labeled_path if got_labeled else None,
        merged_path,
    )
    print(f"  Merged: {total} rows ({labeled_count} with labels)")
    print(f"  Saved to: {merged_path}")

    print_summary(merged_path)


def main():
    parser = argparse.ArgumentParser(
        description="Fetch Apollo alerts via GitHub Actions (no AWS/VPN needed)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--customer", default="CUST-LSUAM",
                        help="Customer ID (default: CUST-LSUAM)")
    parser.add_argument("--mode", choices=["auto", "unlabeled", "triaged", "labeled"], default="auto",
                        help="auto = fetches labeled + unlabeled, merges into one file (default: auto)")
    parser.add_argument("--min-samples", default="100",
                        help="Minimum labeled samples expected (default: 100)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output CSV path (for single-mode only)")
    parser.add_argument("--timeout", type=int, default=300,
                        help="Max seconds to wait for workflow (default: 300)")
    parser.add_argument("--run-id", type=int, default=None,
                        help="Download from an existing run instead of triggering a new one")
    args = parser.parse_args()

    global TIMEOUT
    TIMEOUT = args.timeout

    check_gh_available()

    # Auto mode
    if args.mode == "auto" and not args.run_id:
        run_auto_mode(args.customer, args.min_samples)
        return

    # Single mode
    safe_cust = args.customer.lower().replace("-", "_")
    output_path = args.output or f"{safe_cust}_{args.mode}.csv"

    if args.run_id:
        run_id = args.run_id
        print(f"Using existing run {run_id}")
    else:
        print(f"[1/3] Triggering workflow (customer={args.customer}, mode={args.mode})...")
        trigger_workflow(args.customer, args.mode, args.min_samples)
        time.sleep(3)
        recent = find_recent_runs(limit=1)
        if not recent:
            print("ERROR: Could not find the triggered run.", file=sys.stderr)
            sys.exit(1)
        run_id = recent[0]["databaseId"]
        print(f"  Run ID: {run_id}")

    print(f"[2/3] Waiting for run {run_id}...")
    conclusions = wait_for_runs([run_id])

    if conclusions.get(run_id) != "success":
        print(f"\n  Run failed: {conclusions.get(run_id)}", file=sys.stderr)
        if args.mode == "labeled":
            print("  NOTE: labeled mode needs analyst feedback data. Try --mode auto.", file=sys.stderr)
        sys.exit(1)

    print(f"[3/3] Downloading...")
    if not try_download_artifact(run_id, args.customer, output_path):
        print("ERROR: No data in artifact.", file=sys.stderr)
        sys.exit(1)

    print(f"  Saved to: {output_path}")
    print_summary(output_path)


if __name__ == "__main__":
    main()
