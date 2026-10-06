"""
Preprocess raw Apollo alerts CSV into a flat, ML-ready DataFrame.

Expands the nested JSON columns (stix_features, splunk_features,
derived_features) into individual columns and engineers features
useful for the false-positive predictor.

Column naming convention:
    - No prefix:      original columns from the raw CSV (detection_id, customer_id, etc.)
    - stix_*:         extracted from stix_features JSON
    - splunk_*:       extracted from splunk_features JSON (notable fields)
    - apollo_*:      extracted from derived_features JSON (apollo_metadata)
    - eng_*:          engineered/computed by this script

Usage:
    python preprocess.py                                          # auto-finds latest CSV
    python preprocess.py --input cust_lsuam_unlabeled_*.csv       # specific file
    python preprocess.py --output preprocessed.csv                # custom output
    python preprocess.py --input raw.csv --output clean.csv
"""

import argparse
import csv
import glob
import json
import os
import re
import sys
from datetime import datetime


DEFAULT_INPUT = "cust_lsuam_splunk_alert_data.csv"
DEFAULT_OUTPUT = "cust_lsuam_splunk_alert_data_preprocessed.csv"


def find_input_csv() -> str:
    """Find the input CSV — prefer the merged file, fall back to any available."""
    if os.path.exists(DEFAULT_INPUT):
        return DEFAULT_INPUT
    candidates = glob.glob("cust_*_splunk_alert_data.csv") + glob.glob("cust_*_unlabeled.csv")
    if not candidates:
        print(f"ERROR: No input CSV found. Run fetch_alerts.py first or use --input.", file=sys.stderr)
        sys.exit(1)
    return max(candidates, key=os.path.getmtime)


def load_raw(path: str) -> list[dict]:
    """Load the raw CSV and return rows as dicts."""
    with open(path, "r") as f:
        return list(csv.DictReader(f))


def safe_json(val: str) -> dict | list | None:
    """Parse a JSON string, returning None on failure."""
    if not val or val == "":
        return None
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return None


def extract_stix_fields(stix: dict | None) -> dict:
    """Extract flat fields from a STIX 2.1 bundle. All keys prefixed stix_."""
    out = {}
    if not stix or "objects" not in stix:
        return out

    objects = stix["objects"]

    # MITRE techniques from attack-pattern objects
    mitre = []
    for obj in objects:
        if obj.get("type") == "attack-pattern":
            for ref in obj.get("external_references", []):
                ext_id = ref.get("external_id", "")
                if ext_id:
                    mitre.append(ext_id)
    out["stix_mitre_techniques"] = ";".join(mitre) if mitre else ""
    out["stix_mitre_technique_count"] = len(mitre)

    # Extract from the indicator object
    for obj in objects:
        if obj.get("type") != "indicator":
            continue

        out["stix_alert_name"] = obj.get("name", "")
        out["stix_alert_description"] = obj.get("description", "")

        # Extension fields
        for ext in obj.get("extensions", {}).values():
            out["stix_apollo_severity"] = ext.get("apollo_severity", "")
            out["stix_vendor_severity"] = ext.get("vendor_severity", "")
            out["stix_vendor_alert_id"] = ext.get("vendor_alert_id", "")
            out["stix_vendor_alert_name"] = ext.get("vendor_alert_name", "")
            out["stix_vendor_technique"] = ext.get("vendor_technique", "")

            vs = ext.get("vendor_specific", {})
            out["stix_search_name"] = vs.get("search_name", "")
            out["stix_rule_description"] = vs.get("rule_description", "")
            out["stix_client_name"] = vs.get("client_name", "")

            # MITRE from annotations (backup)
            nf = vs.get("notable_fields", {})
            annot = nf.get("annotations.mitre_attack", "")
            if annot and not out["stix_mitre_techniques"]:
                out["stix_mitre_techniques"] = annot

        break  # only need first indicator

    # Count observable objects
    obs_count = sum(1 for o in objects if o.get("type") in (
        "ipv4-addr", "ipv6-addr", "domain-name", "url",
        "email-addr", "user-account", "file",
    ))
    out["stix_observable_count"] = obs_count

    return out


def extract_splunk_fields(stix: dict | None) -> dict:
    """Extract notable fields from Splunk via the STIX indicator extension. All keys prefixed splunk_."""
    out = {}
    if not stix or "objects" not in stix:
        return out

    for obj in stix["objects"]:
        if obj.get("type") != "indicator":
            continue
        for ext in obj.get("extensions", {}).values():
            nf = ext.get("vendor_specific", {}).get("notable_fields", {})
            if not nf:
                continue

            # Core
            out["splunk_orig_rule_title"] = nf.get("orig_rule_title", "")
            out["splunk_orig_security_domain"] = nf.get("orig_security_domain", "")
            out["splunk_severity"] = nf.get("severity", "")
            out["splunk_time"] = nf.get("_time", "")

            # Network
            out["splunk_source_address"] = nf.get("sourceAddress", "")
            out["splunk_destination_address"] = nf.get("destinationAddress", "")
            out["splunk_source_port"] = nf.get("sourcePort", "")
            out["splunk_destination_port"] = nf.get("destinationPort", "")
            out["splunk_src_host"] = nf.get("shost", "")

            # User/Identity
            out["splunk_user"] = nf.get("user", "") or nf.get("User", "") or nf.get("destinationUserName", "")
            out["splunk_user_principal_name"] = nf.get("userPrincipalName", "")
            out["splunk_src_user"] = nf.get("src_user", "")
            out["splunk_dest_user"] = nf.get("dest_user", "")
            out["splunk_account_name"] = nf.get("accountName", "")

            # Defender
            out["splunk_defender_category"] = nf.get("category", "")
            out["splunk_defender_classification"] = nf.get("classification", "")
            out["splunk_defender_detection_source"] = nf.get("detectionSource", "")
            out["splunk_defender_investigation_state"] = nf.get("investigationState", "")
            out["splunk_defender_title"] = nf.get("title", "")

            # Auth / Brute force
            out["splunk_total_failures"] = nf.get("totalFailures", "")
            out["splunk_total_successes"] = nf.get("totalSuccesses", "")
            out["splunk_logon_error"] = nf.get("LogonError", "")
            out["splunk_computer_name"] = nf.get("Computer_Name", "")

            # Geo
            out["splunk_country"] = nf.get("Country", "") or nf.get("current_country", "")
            out["splunk_city"] = nf.get("City", "")
            out["splunk_lat"] = nf.get("lat", "")
            out["splunk_lon"] = nf.get("lon", "")

            # IOC
            out["splunk_ioc"] = nf.get("IOC", "")
            out["splunk_ioc_type"] = nf.get("IOC_Type", "")

            # Risk
            out["splunk_risk_score"] = nf.get("risk_score", "")
            out["splunk_risk_object"] = nf.get("risk_object", "")
            out["splunk_risk_object_type"] = nf.get("risk_object_type", "")

            # File/Process
            out["splunk_file_name"] = nf.get("fileName", "")
            out["splunk_file_hash_sha256"] = nf.get("fileHashSha256", "") or nf.get("sha256", "")
            out["splunk_process_command_line"] = nf.get("processCommandLine", "")
            out["splunk_device_hostname"] = nf.get("deviceHostname", "") or nf.get("deviceDnsName", "")

            # Misc
            out["splunk_ports_scanned"] = nf.get("ports_scanned", "")
            out["splunk_is_acknowledged"] = nf.get("is_acknowledged", "")

            break
        break

    return out


def extract_apollo_fields(derived: dict | None) -> dict:
    """Extract fields from apollo_metadata / derived_features. All keys prefixed apollo_."""
    out = {}
    if not derived:
        return out

    out["apollo_ingested_at"] = derived.get("ingestedAt", "")
    out["apollo_normalized_at"] = derived.get("normalizedAt", "")
    out["apollo_source_alert_id"] = derived.get("sourceAlertId", "")
    out["apollo_correlation_id"] = derived.get("correlationId", "")

    return out


def engineer_features(row: dict) -> dict:
    """Compute derived ML features. All keys prefixed eng_."""
    out = {}

    # Severity as numeric
    sev_map = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0, "informational": 0}
    out["eng_severity_numeric"] = sev_map.get((row.get("stix_apollo_severity") or "").lower(), -1)

    # IOC type classification from splunk_ioc value
    ioc_val = row.get("splunk_ioc", "").strip()
    if ioc_val:
        if re.match(r"^https?://", ioc_val):
            out["eng_ioc_type"] = "URL"
        elif "@" in ioc_val and "." in ioc_val:
            out["eng_ioc_type"] = "Email"
        elif re.match(r"^[0-9a-fA-F]{32,128}$", ioc_val):
            out["eng_ioc_type"] = "Hash"
        elif ":" in ioc_val:
            out["eng_ioc_type"] = "IPv6"
        elif re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", ioc_val):
            out["eng_ioc_type"] = "IPv4"
        else:
            out["eng_ioc_type"] = "Other"
    else:
        out["eng_ioc_type"] = ""

    # Binary presence flags
    out["eng_has_source_ip"] = 1 if row.get("splunk_source_address") else 0
    out["eng_has_dest_ip"] = 1 if row.get("splunk_destination_address") else 0
    out["eng_has_user"] = 1 if row.get("splunk_user") else 0
    out["eng_has_ioc"] = 1 if row.get("splunk_ioc") else 0
    out["eng_has_geo"] = 1 if row.get("splunk_country") else 0
    out["eng_has_file_hash"] = 1 if row.get("splunk_file_hash_sha256") else 0

    # Alert category from search_name
    search = row.get("stix_search_name", "")
    if "Brute Force" in search:
        out["eng_alert_category"] = "brute_force"
    elif "Port scan" in search or "Canary" in search:
        out["eng_alert_category"] = "port_scan"
    elif "Failed Login" in search:
        out["eng_alert_category"] = "failed_login"
    elif "Suspicious" in search:
        out["eng_alert_category"] = "suspicious_activity"
    elif "Defender" in search:
        out["eng_alert_category"] = "defender_alert"
    elif "Heartbeat" in search:
        out["eng_alert_category"] = "heartbeat"
    elif "Risk" in search:
        out["eng_alert_category"] = "risk_threshold"
    elif "Added to a role" in search or "Rights Delegation" in search:
        out["eng_alert_category"] = "identity_change"
    elif "Login from Suspicious" in search:
        out["eng_alert_category"] = "suspicious_login"
    else:
        out["eng_alert_category"] = "other"

    # Threat type from first word of stix_alert_name before " - "
    alert_name = row.get("stix_alert_name", "").strip()
    if " - " in alert_name:
        out["eng_detection_category"] = alert_name.split(" - ")[0].strip().lower()
    else:
        out["eng_detection_category"] = "unclassified"

    # Detection domain from stix_search_name (exact rule name matching)
    IDENTITY_RULES = [
        "Threat - LSU - Azure Audit - Login from Suspicious Country - Rule",
        "Threat - LSU - Azure Audit - Failed Logins (Outside of USA)  - Rule",
        "Threat - LSU - Windows - Basic Brute Force Detection - Rule",
        "Access - LSU - Azure - Basic Brute Force Detection - Rule",
        "Identity - LSU - O365 - O365 Suspicious Rights Delegation - Rule",
        "Identity - LSU - Azure Audit - Users Added to a role - Rule",
    ]
    ENDPOINT_RULES = [
        "Threat - LSU - Defender - New High Severity Alerts - Rule",
    ]
    NETWORK_RULES = [
        "Threat - LSU - Canary - Port scan on Canary Device - Rule",
        "Threat - LSU - Palo Alto - High and Critical Threat Allowed - Rule",
    ]
    search = row.get("stix_search_name", "").strip()
    if search in IDENTITY_RULES:
        out["eng_detection_domain"] = "Identity"
    elif search in ENDPOINT_RULES:
        out["eng_detection_domain"] = "Endpoint"
    elif search in NETWORK_RULES:
        out["eng_detection_domain"] = "Network"
    else:
        out["eng_detection_domain"] = "Unclassified"

    # Temporal features from splunk_time (CDT)
    splunk_time = row.get("splunk_time", "")
    if splunk_time:
        try:
            parts = splunk_time.rsplit(" ", 1)[0]
            dt = datetime.strptime(parts, "%Y-%m-%d %H:%M:%S.%f")
            out["eng_event_date"] = dt.strftime("%Y-%m-%d")
            out["eng_hour_of_day"] = dt.hour
            out["eng_day_of_week"] = dt.weekday()
            out["eng_is_business_hours"] = 1 if 8 <= dt.hour <= 17 and dt.weekday() < 5 else 0
        except ValueError:
            out["eng_event_date"] = ""
            out["eng_hour_of_day"] = ""
            out["eng_day_of_week"] = ""
            out["eng_is_business_hours"] = ""

    return out


# --- Column lists (define output order) ---

# Original raw CSV columns — prefixed org_, names match DB column names
RAW_COLS = [
    "org_id",
    "org_customer_id",
    "org_source",
    "org_severity",
    "org_status",
    "org_stix_bundle",
    "org_raw_payload",
    "org_apollo_metadata",
    "org_dedup_count",
    "org_created_at",
]

# Maps raw CSV column names (DB column names) to our prefixed names
RAW_COL_MAP = {
    "id": "org_id",
    "customer_id": "org_customer_id",
    "source": "org_source",
    "severity": "org_severity",
    "status": "org_status",
    "stix_bundle": "org_stix_bundle",
    "raw_payload": "org_raw_payload",
    "apollo_metadata": "org_apollo_metadata",
    "dedup_count": "org_dedup_count",
    "created_at": "org_created_at",
}

# Extracted from stix_features JSON
STIX_COLS = [
    "stix_alert_name",
    "stix_alert_description",
    "stix_search_name",
    "stix_rule_description",
    "stix_client_name",
    "stix_apollo_severity",
    "stix_vendor_severity",
    "stix_vendor_alert_id",
    "stix_vendor_alert_name",
    "stix_vendor_technique",
    "stix_mitre_techniques",
    "stix_mitre_technique_count",
    "stix_observable_count",
]

# Extracted from splunk notable fields (via stix_features.indicator.extension.vendor_specific.notable_fields)
SPLUNK_COLS = [
    "splunk_orig_rule_title",
    "splunk_orig_security_domain",
    "splunk_severity",
    "splunk_time",
    # Network
    "splunk_source_address",
    "splunk_destination_address",
    "splunk_source_port",
    "splunk_destination_port",
    "splunk_src_host",
    # User/Identity
    "splunk_user",
    "splunk_user_principal_name",
    "splunk_src_user",
    "splunk_dest_user",
    "splunk_account_name",
    # Defender
    "splunk_defender_category",
    "splunk_defender_classification",
    "splunk_defender_detection_source",
    "splunk_defender_investigation_state",
    "splunk_defender_title",
    # Auth/Brute force
    "splunk_total_failures",
    "splunk_total_successes",
    "splunk_logon_error",
    "splunk_computer_name",
    # Geo
    "splunk_country",
    "splunk_city",
    "splunk_lat",
    "splunk_lon",
    # IOC
    "splunk_ioc",
    "splunk_ioc_type",
    # Risk
    "splunk_risk_score",
    "splunk_risk_object",
    "splunk_risk_object_type",
    # File/Process
    "splunk_file_name",
    "splunk_file_hash_sha256",
    "splunk_process_command_line",
    "splunk_device_hostname",
    # Misc
    "splunk_ports_scanned",
    "splunk_is_acknowledged",
]

# Extracted from derived_features JSON (apollo_metadata)
DERIVED_COLS = [
    "apollo_ingested_at",
    "apollo_normalized_at",
    "apollo_source_alert_id",
    "apollo_correlation_id",
]

# Engineered/computed by this script
ENGINEERED_COLS = [
    "eng_severity_numeric",
    "eng_ioc_type",
    "eng_alert_category",
    "eng_has_source_ip",
    "eng_has_dest_ip",
    "eng_has_user",
    "eng_has_ioc",
    "eng_has_geo",
    "eng_has_file_hash",
    "eng_detection_category",
    "eng_detection_domain",
    "eng_event_date",
    "eng_hour_of_day",
    "eng_day_of_week",
    "eng_is_business_hours",
]

ALL_COLS = RAW_COLS + STIX_COLS + SPLUNK_COLS + DERIVED_COLS + ENGINEERED_COLS


def preprocess(rows: list[dict]) -> list[dict]:
    """Transform raw rows into flat, ML-ready rows."""
    output = []

    for raw in rows:
        row = {}

        # Original columns (pass through with org_ prefix)
        for orig, prefixed in RAW_COL_MAP.items():
            row[prefixed] = raw.get(orig, "")

        # Expand stix_bundle
        stix = safe_json(raw.get("stix_bundle", ""))
        stix_fields = extract_stix_fields(stix)
        for col in STIX_COLS:
            row[col] = stix_fields.get(col, "")

        # Expand splunk notable fields
        splunk_fields = extract_splunk_fields(stix)
        for col in SPLUNK_COLS:
            row[col] = splunk_fields.get(col, "")

        # Expand apollo_metadata
        derived = safe_json(raw.get("apollo_metadata", ""))
        apollo_fields = extract_apollo_fields(derived)
        for col in DERIVED_COLS:
            row[col] = apollo_fields.get(col, "")

        # Engineer features
        eng = engineer_features(row)
        for col in ENGINEERED_COLS:
            row[col] = eng.get(col, "")

        output.append(row)

    return output


def write_output(rows: list[dict], path: str):
    """Write preprocessed rows to CSV."""
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ALL_COLS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def print_summary(rows: list[dict]):
    """Print preprocessing summary."""
    n = len(rows)
    print(f"\n  Rows: {n}")
    print(f"  Columns: {len(ALL_COLS)}")
    print(f"    org_*:             {len(RAW_COLS)}")
    print(f"    stix_*:            {len(STIX_COLS)}")
    print(f"    splunk_*:          {len(SPLUNK_COLS)}")
    print(f"    apollo_*:         {len(DERIVED_COLS)}")
    print(f"    eng_*:             {len(ENGINEERED_COLS)}")

    # Coverage for key extracted fields
    print("\n  Field coverage:")
    check_fields = [
        "stix_alert_name", "stix_search_name", "stix_mitre_techniques",
        "splunk_source_address", "splunk_destination_address", "splunk_user",
        "splunk_country", "splunk_ioc", "splunk_defender_title",
        "splunk_total_failures", "splunk_risk_score", "splunk_file_hash_sha256",
    ]
    for f in check_fields:
        filled = sum(1 for r in rows if r.get(f))
        pct = filled / n * 100 if n else 0
        print(f"    {f:<36} {filled:>4}/{n} ({pct:.0f}%)")

    # Engineered alert category distribution
    cats = {}
    for r in rows:
        c = r.get("eng_alert_category", "unknown")
        cats[c] = cats.get(c, 0) + 1
    print("\n  eng_alert_category distribution:")
    for c, count in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"    {c:<24} {count:>4} ({count/n*100:.0f}%)")

    print()


def main():
    parser = argparse.ArgumentParser(description="Preprocess Apollo alerts CSV for ML")
    parser.add_argument("--input", type=str, default=None, help="Input CSV (auto-detects latest if omitted)")
    parser.add_argument("--output", type=str, default=None, help="Output CSV (auto-generated if omitted)")
    args = parser.parse_args()

    input_path = args.input or find_input_csv()
    print(f"[1/3] Loading {input_path}...")
    raw_rows = load_raw(input_path)
    print(f"  {len(raw_rows)} rows loaded")

    print(f"[2/3] Preprocessing...")
    processed = preprocess(raw_rows)

    output_path = args.output or DEFAULT_OUTPUT

    write_output(processed, output_path)
    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    print(f"\n[3/3] Wrote {output_path} ({size_mb:.1f} MB)")

    print_summary(processed)


if __name__ == "__main__":
    main()
