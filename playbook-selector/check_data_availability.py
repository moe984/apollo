"""
Check cloud database for real data availability for Playbook Selector model training.

Prerequisites:
    1. AWS CLI installed: brew install awscli
    2. AWS credentials configured: aws configure
    3. psql installed: brew install postgresql

Usage:
    python check_data_availability.py                    # auto-fetch creds from Secrets Manager
    python check_data_availability.py --db-url "postgresql://user:pass@host:5432/db"  # manual
    python check_data_availability.py --output report.json
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime


def get_db_url_from_secrets_manager() -> str:
    """Fetch database credentials from AWS Secrets Manager."""
    print("[1/6] Fetching credentials from AWS Secrets Manager...")
    try:
        result = subprocess.run(
            [
                "aws", "secretsmanager", "get-secret-value",
                "--secret-id", "mdr-plus/dev/db-credentials",
                "--query", "SecretString",
                "--output", "text",
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            print(f"  ERROR: {result.stderr.strip()}", file=sys.stderr)
            print("  Make sure AWS CLI is configured: aws configure", file=sys.stderr)
            sys.exit(1)

        creds = json.loads(result.stdout.strip())
        url = f"postgresql://{creds['username']}:{creds['password']}@{creds['host']}:{creds['port']}/{creds['database']}"
        print(f"  Connected to: {creds['host']}:{creds['port']}/{creds['database']}")
        return url

    except FileNotFoundError:
        print("  ERROR: aws CLI not found. Install with: brew install awscli", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError:
        print("  ERROR: Could not parse secret value as JSON", file=sys.stderr)
        sys.exit(1)


def run_query(db_url: str, query: str) -> list[dict]:
    """Run a SQL query via psql and return results as list of dicts."""
    try:
        result = subprocess.run(
            ["psql", db_url, "-t", "-A", "-F", "\t", "-c", query],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if "does not exist" in stderr:
                return []
            print(f"  Query error: {stderr}", file=sys.stderr)
            return []

        lines = [l for l in result.stdout.strip().split("\n") if l]
        return lines

    except FileNotFoundError:
        print("  ERROR: psql not found. Install with: brew install postgresql", file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("  ERROR: Query timed out", file=sys.stderr)
        return []


def check_schema_exists(db_url: str, schema: str) -> bool:
    """Check if a schema exists."""
    rows = run_query(db_url, f"SELECT 1 FROM information_schema.schemata WHERE schema_name = '{schema}';")
    return len(rows) > 0


def check_table(db_url: str, table: str, label: str) -> dict:
    """Check a table's row count and date range."""
    count_rows = run_query(db_url, f"SELECT COUNT(*) FROM {table};")
    count = int(count_rows[0]) if count_rows else 0

    result = {"table": table, "label": label, "count": count}

    if count > 0:
        # Try to get date range
        for col in ["created_at", "feature_time", "timestamp", "feedback_time"]:
            range_rows = run_query(
                db_url,
                f"SELECT MIN({col})::text, MAX({col})::text FROM {table};",
            )
            if range_rows and "\t" in range_rows[0]:
                parts = range_rows[0].split("\t")
                result["earliest"] = parts[0]
                result["latest"] = parts[1]
                break

    return result


def main():
    parser = argparse.ArgumentParser(description="Check cloud DB for Playbook Selector training data")
    parser.add_argument("--db-url", type=str, default=None, help="PostgreSQL connection URL (skips Secrets Manager)")
    parser.add_argument("--output", type=str, default=None, help="Save report as JSON")
    args = parser.parse_args()

    db_url = args.db_url or get_db_url_from_secrets_manager()
    report = {"timestamp": datetime.utcnow().isoformat(), "checks": {}}

    # =========================================================================
    # Check 1: Schema existence
    # =========================================================================
    print("\n[2/6] Checking schema existence...")
    schemas = {}
    for schema in ["intelligence", "workflow", "public"]:
        exists = check_schema_exists(db_url, schema)
        schemas[schema] = exists
        status = "EXISTS" if exists else "MISSING"
        print(f"  {schema:<20} {status}")
    report["checks"]["schemas"] = schemas

    if not schemas.get("intelligence"):
        print("\n  FATAL: intelligence schema does not exist. Migrations not applied.")
        print("  Cannot proceed with data checks.")
        report["checks"]["fatal"] = "intelligence schema missing"
        if args.output:
            with open(args.output, "w") as f:
                json.dump(report, f, indent=2)
        sys.exit(1)

    # =========================================================================
    # Check 2: Incident features (model training input)
    # =========================================================================
    print("\n[3/6] Checking incident features (training input)...")
    incident_features = check_table(db_url, "intelligence.incident_features", "Incident features")
    print(f"  Rows: {incident_features['count']}")
    if incident_features.get("earliest"):
        print(f"  Range: {incident_features['earliest']} -> {incident_features['latest']}")

    # Check how many have playbook-related labels
    labeled_rows = run_query(
        db_url,
        "SELECT COUNT(*) FROM intelligence.incident_features WHERE labels->>'playbookEffective' IS NOT NULL;",
    )
    labeled_count = int(labeled_rows[0]) if labeled_rows else 0
    incident_features["labeled_count"] = labeled_count
    print(f"  With playbookEffective label: {labeled_count}")

    # Customer breakdown
    customer_rows = run_query(
        db_url,
        "SELECT customer_id, COUNT(*) FROM intelligence.incident_features GROUP BY customer_id ORDER BY COUNT(*) DESC LIMIT 10;",
    )
    if customer_rows:
        incident_features["customers"] = []
        print("  Per customer:")
        for row in customer_rows:
            parts = row.split("\t")
            incident_features["customers"].append({"customer_id": parts[0], "count": int(parts[1])})
            print(f"    {parts[0]:<30} {parts[1]:>6} rows")

    report["checks"]["incident_features"] = incident_features

    # =========================================================================
    # Check 3: Feedback data (training labels)
    # =========================================================================
    print("\n[4/6] Checking feedback data (training labels)...")

    # Explicit incident feedback
    incident_feedback = check_table(db_url, "intelligence.incident_feedback", "Incident feedback")
    print(f"  incident_feedback rows: {incident_feedback['count']}")

    playbook_feedback_rows = run_query(
        db_url,
        "SELECT COUNT(*) FROM intelligence.incident_feedback WHERE playbook_effective IS NOT NULL;",
    )
    playbook_feedback_count = int(playbook_feedback_rows[0]) if playbook_feedback_rows else 0
    incident_feedback["with_playbook_effective"] = playbook_feedback_count
    print(f"  With playbook_effective: {playbook_feedback_count}")

    playbook_id_rows = run_query(
        db_url,
        "SELECT COUNT(*) FROM intelligence.incident_feedback WHERE playbook_id IS NOT NULL;",
    )
    playbook_id_count = int(playbook_id_rows[0]) if playbook_id_rows else 0
    incident_feedback["with_playbook_id"] = playbook_id_count
    print(f"  With playbook_id: {playbook_id_count}")

    if playbook_feedback_count > 0:
        # Label distribution
        dist_rows = run_query(
            db_url,
            "SELECT playbook_effective, COUNT(*) FROM intelligence.incident_feedback WHERE playbook_effective IS NOT NULL GROUP BY playbook_effective;",
        )
        if dist_rows:
            incident_feedback["label_distribution"] = {}
            print("  Label distribution:")
            for row in dist_rows:
                parts = row.split("\t")
                label = "effective" if parts[0] == "t" else "not_effective"
                incident_feedback["label_distribution"][label] = int(parts[1])
                print(f"    {label:<20} {parts[1]:>6}")

    # AI recommendation feedback
    ai_feedback = check_table(db_url, "intelligence.ai_feedback", "AI feedback")
    print(f"  ai_feedback rows: {ai_feedback['count']}")

    playbook_suggestion_rows = run_query(
        db_url,
        "SELECT COUNT(*), SUM(CASE WHEN accepted THEN 1 ELSE 0 END) FROM intelligence.ai_feedback WHERE recommendation_type = 'playbook_suggestion';",
    )
    if playbook_suggestion_rows and "\t" in playbook_suggestion_rows[0]:
        parts = playbook_suggestion_rows[0].split("\t")
        ai_feedback["playbook_suggestions"] = int(parts[0])
        ai_feedback["playbook_accepted"] = int(parts[1]) if parts[1] else 0
        print(f"  Playbook suggestions: {parts[0]} (accepted: {parts[1] or 0})")

    # Implicit feedback
    implicit_feedback = check_table(db_url, "intelligence.implicit_feedback", "Implicit feedback")
    print(f"  implicit_feedback rows: {implicit_feedback['count']}")

    playbook_implicit_rows = run_query(
        db_url,
        "SELECT action_type, COUNT(*) FROM intelligence.implicit_feedback WHERE action_type IN ('playbook_cancelled', 'playbook_modified') GROUP BY action_type;",
    )
    if playbook_implicit_rows:
        implicit_feedback["playbook_actions"] = {}
        print("  Playbook implicit signals:")
        for row in playbook_implicit_rows:
            parts = row.split("\t")
            implicit_feedback["playbook_actions"][parts[0]] = int(parts[1])
            print(f"    {parts[0]:<25} {parts[1]:>6}")

    report["checks"]["incident_feedback"] = incident_feedback
    report["checks"]["ai_feedback"] = ai_feedback
    report["checks"]["implicit_feedback"] = implicit_feedback

    # =========================================================================
    # Check 4: Playbook catalog
    # =========================================================================
    print("\n[5/6] Checking playbook catalog...")

    if schemas.get("workflow"):
        templates = check_table(db_url, "workflow.playbook_templates", "Playbook templates")
        print(f"  Templates: {templates['count']}")

        if templates["count"] > 0:
            template_rows = run_query(
                db_url,
                "SELECT playbook_id, name, category FROM workflow.playbook_templates ORDER BY name;",
            )
            if template_rows:
                templates["items"] = []
                for row in template_rows:
                    parts = row.split("\t")
                    templates["items"].append({"id": parts[0], "name": parts[1], "category": parts[2]})
                    print(f"    {parts[1]:<35} ({parts[2]})")

        instances = check_table(db_url, "workflow.playbook_instances", "Playbook instances")
        print(f"  Customer instances: {instances['count']}")

        if instances["count"] > 0:
            instance_customer_rows = run_query(
                db_url,
                "SELECT customer_id, COUNT(*) FROM workflow.playbook_instances GROUP BY customer_id ORDER BY COUNT(*) DESC LIMIT 10;",
            )
            if instance_customer_rows:
                instances["per_customer"] = []
                print("  Per customer:")
                for row in instance_customer_rows:
                    parts = row.split("\t")
                    instances["per_customer"].append({"customer_id": parts[0], "count": int(parts[1])})
                    print(f"    {parts[0]:<30} {parts[1]:>6}")

        report["checks"]["playbook_templates"] = templates
        report["checks"]["playbook_instances"] = instances
    else:
        print("  SKIPPED: workflow schema does not exist")
        report["checks"]["playbook_templates"] = {"count": 0, "error": "workflow schema missing"}

    # =========================================================================
    # Check 5: Training history
    # =========================================================================
    print("\n[6/6] Checking training history...")

    training_jobs_rows = run_query(
        db_url,
        "SELECT COUNT(*) FROM intelligence.training_jobs WHERE model_id = 'playbook-selector';",
    )
    training_count = int(training_jobs_rows[0]) if training_jobs_rows else 0
    print(f"  Past training jobs: {training_count}")

    if training_count > 0:
        job_detail_rows = run_query(
            db_url,
            "SELECT job_id, status, started_at::text, train_samples, test_samples FROM intelligence.training_jobs WHERE model_id = 'playbook-selector' ORDER BY started_at DESC LIMIT 5;",
        )
        if job_detail_rows:
            report["checks"]["training_jobs"] = []
            print("  Recent jobs:")
            for row in job_detail_rows:
                parts = row.split("\t")
                print(f"    {parts[2]:<22} status={parts[1]:<10} train={parts[3]} test={parts[4]}")
    else:
        report["checks"]["training_jobs"] = {"count": 0}

    # =========================================================================
    # Verdict
    # =========================================================================
    print("\n" + "=" * 70)
    print("VERDICT: Can we train a real Playbook Selector model?")
    print("=" * 70)

    feat_count = incident_features["count"]
    label_count = playbook_feedback_count + incident_features.get("labeled_count", 0)
    implicit_count = implicit_feedback["count"]
    catalog_count = report["checks"].get("playbook_templates", {}).get("count", 0)

    blockers = []

    if feat_count == 0:
        blockers.append("NO incident feature data in the database")
    elif feat_count < 100:
        blockers.append(f"Only {feat_count} incident features — need at least a few hundred")

    if label_count == 0 and implicit_count == 0:
        blockers.append("NO feedback labels (explicit or implicit) — nothing to train on")
    elif label_count < 50 and implicit_count < 50:
        blockers.append(f"Only {label_count} explicit + {implicit_count} implicit labels — very thin")

    if catalog_count == 0:
        blockers.append("NO playbook templates in catalog — model has nothing to recommend")

    verdict = {
        "can_train": len(blockers) == 0,
        "incident_features": feat_count,
        "explicit_labels": label_count,
        "implicit_signals": implicit_count,
        "playbook_templates": catalog_count,
        "blockers": blockers,
    }
    report["verdict"] = verdict

    if blockers:
        print("\n  CANNOT train yet. Blockers:")
        for b in blockers:
            print(f"    - {b}")
    else:
        print(f"\n  YES — sufficient data exists:")
        print(f"    Incident features:  {feat_count}")
        print(f"    Feedback labels:    {label_count}")
        print(f"    Implicit signals:   {implicit_count}")
        print(f"    Playbook templates: {catalog_count}")

    print()

    if args.output:
        with open(args.output, "w") as f:
            json.dump(report, f, indent=2)
        print(f"Full report saved to {args.output}")


if __name__ == "__main__":
    main()
