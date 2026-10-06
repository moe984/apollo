"""
Find false positives in hash dedup pairs.

Reads dedup_input.csv. For each hash-dedup pair, compares the raw_payload
fields that SHOULD have been in the hash but weren't. Outputs a CSV
with one row per pair showing what matched and what didn't.

Output: dedup/data/fp_analysis.csv

Usage:
    python find_fp.py
"""

import csv
import json
import os
import re

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")


def parse_splunk_raw(raw_payload: dict) -> dict:
    raw_str = raw_payload.get("_raw", "")
    fields = {}
    for match in re.finditer(r'(\w+)="([^"]*)"|(\w+)=(\S+)', raw_str):
        k = match.group(1) or match.group(3)
        v = match.group(2) or match.group(4)
        fields[k] = v
    for k, v in raw_payload.items():
        if isinstance(v, str) and k not in fields:
            fields[k] = v
    return fields


def main():
    input_path = os.path.join(DATA_DIR, "dedup_input.csv")
    with open(input_path) as f:
        all_alerts = list(csv.DictReader(f))
    by_id = {r["id"]: r for r in all_alerts}

    # Find all alerts that have a parent (were deduped)
    deduped = [r for r in all_alerts if r["parent_alert_id"] and r["parent_alert_id"] in by_id]

    rows_out = []
    for dup in deduped:
        can = by_id[dup["parent_alert_id"]]

        # Parse raw payloads
        try:
            dup_fields = parse_splunk_raw(json.loads(dup["raw_payload"]))
            can_fields = parse_splunk_raw(json.loads(can["raw_payload"]))
        except:
            continue

        # Check if test alert
        try:
            dup_meta = json.loads(dup["apollo_metadata"])
            can_meta = json.loads(can["apollo_metadata"])
        except:
            dup_meta, can_meta = {}, {}

        is_test = (
            str(can_meta.get("sourceAlertId", "")).startswith("dedup-test") or
            str(dup_meta.get("sourceAlertId", "")).startswith("dedup-test")
        )

        # Extract the fields that matter
        can_src = can_fields.get("src_ip", can_fields.get("src", ""))
        can_dest = can_fields.get("dest_ip", can_fields.get("dest", ""))
        can_ioc = can_fields.get("IOC", "")
        can_ioc_type = can_fields.get("IOC_Type", "")
        can_category = can_fields.get("search_name", can_fields.get("rule_name", ""))
        can_title = can_fields.get("title", "")
        can_account = can_fields.get("accountName", "")

        dup_src = dup_fields.get("src_ip", dup_fields.get("src", ""))
        dup_dest = dup_fields.get("dest_ip", dup_fields.get("dest", ""))
        dup_ioc = dup_fields.get("IOC", "")
        dup_ioc_type = dup_fields.get("IOC_Type", "")
        dup_category = dup_fields.get("search_name", dup_fields.get("rule_name", ""))
        dup_title = dup_fields.get("title", "")
        dup_account = dup_fields.get("accountName", "")

        # What went into the hash
        hash_src = can_src or (can_ioc if can_ioc_type == "IP" else "")
        hash_dest = can_dest
        hash_category = can_category

        # Time gap
        try:
            from datetime import datetime
            t1 = datetime.fromisoformat(can["created_at"].strip('"').replace("Z", "+00:00"))
            t2 = datetime.fromisoformat(dup["created_at"].strip('"').replace("Z", "+00:00"))
            gap_s = round(abs((t2 - t1).total_seconds()), 1)
        except:
            gap_s = ""

        # Compare: what's same, what's different
        title_match = "same" if can_title == dup_title else ("both_empty" if not can_title and not dup_title else "different")
        ioc_match = "same" if can_ioc == dup_ioc else ("both_empty" if not can_ioc and not dup_ioc else "different")
        account_match = "same" if can_account == dup_account else ("both_empty" if not can_account and not dup_account else "different")

        # Verdict: FP when hash had no IPs and the alerts differ on at least one field
        both_ips_empty = hash_src == "" and hash_dest == ""
        any_field_differs = title_match == "different" or ioc_match == "different" or account_match == "different"

        if is_test:
            verdict = "TEST"
        elif both_ips_empty and any_field_differs:
            verdict = "FP"
        elif both_ips_empty and not any_field_differs:
            verdict = "UNKNOWN"
        else:
            verdict = "TP"

        rows_out.append({
            "canonical_id": can["id"],
            "duplicate_id": dup["id"],
            "is_test": is_test,
            "verdict": verdict,
            "gap_seconds": gap_s,
            "hash_src_ip": hash_src,
            "hash_dest_ip": hash_dest,
            "hash_category": hash_category,
            "hash_src_empty": hash_src == "",
            "hash_dest_empty": hash_dest == "",
            "can_title": can_title,
            "dup_title": dup_title,
            "title_match": title_match,
            "can_ioc": can_ioc,
            "dup_ioc": dup_ioc,
            "can_ioc_type": can_ioc_type,
            "dup_ioc_type": dup_ioc_type,
            "ioc_match": ioc_match,
            "can_account": can_account,
            "dup_account": dup_account,
            "account_match": account_match,
            "can_severity": can["severity"],
            "dup_severity": dup["severity"],
        })

    # Write CSV
    out_path = os.path.join(DATA_DIR, "fp_analysis.csv")
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows_out[0].keys())
        writer.writeheader()
        writer.writerows(rows_out)

    # Print summary
    total = len(rows_out)
    verdicts = {}
    for r in rows_out:
        v = r["verdict"]
        verdicts[v] = verdicts.get(v, 0) + 1

    real_rows = [r for r in rows_out if not r["is_test"]]
    real = len(real_rows)
    both_empty = sum(1 for r in real_rows if r["hash_src_empty"] and r["hash_dest_empty"])
    ioc_diff = sum(1 for r in real_rows if r["ioc_match"] == "different")
    title_diff = sum(1 for r in real_rows if r["title_match"] == "different")
    account_diff = sum(1 for r in real_rows if r["account_match"] == "different")

    # Per-canonical (group level) counts
    fp_canonicals = set(r["canonical_id"] for r in real_rows if r["verdict"] == "FP")
    tp_canonicals = set(r["canonical_id"] for r in real_rows if r["verdict"] == "TP")
    unknown_canonicals = set(r["canonical_id"] for r in real_rows if r["verdict"] == "UNKNOWN")

    # Per-canonical: how many dups each FP canonical absorbed
    fp_can_counts = {}
    for r in real_rows:
        if r["verdict"] == "FP":
            cid = r["canonical_id"]
            fp_can_counts[cid] = fp_can_counts.get(cid, 0) + 1

    print(f"Wrote {out_path}")
    print(f"")
    print(f"Total pairs:          {total}")
    print(f"")
    print(f"--- Per Alert (each suppressed alert) ---")
    print(f"  FP:                 {verdicts.get('FP', 0)} alerts wrongly suppressed")
    print(f"  TP:                 {verdicts.get('TP', 0)} alerts correctly suppressed")
    print(f"  UNKNOWN:            {verdicts.get('UNKNOWN', 0)} alerts insufficient data")
    print(f"  TEST:               {verdicts.get('TEST', 0)} test alerts")
    print(f"")
    print(f"--- Per Canonical (each group with a bad hash) ---")
    print(f"  FP groups:          {len(fp_canonicals)} canonicals absorbed wrong alerts")
    print(f"  TP groups:          {len(tp_canonicals)} canonicals absorbed correct duplicates")
    print(f"  UNKNOWN groups:     {len(unknown_canonicals)} canonicals insufficient data")
    print(f"")
    print(f"--- FP Canonicals Breakdown ---")
    for cid, count in sorted(fp_can_counts.items(), key=lambda x: -x[1]):
        print(f"  {cid[:25]}: {count} alerts wrongly suppressed")
    print(f"")
    print(f"Rule: FP = both IPs empty AND (title OR IOC OR account differs)")
    print(f"      TP = hash had src_ip")
    print(f"      UNKNOWN = both IPs empty but no observable difference")
    print(f"")
    print(f"Real pairs ({real}):")
    print(f"  both IPs empty:     {both_empty}/{real}")
    print(f"  different IOC:      {ioc_diff}/{real}")
    print(f"  different title:    {title_diff}/{real}")
    print(f"  different account:  {account_diff}/{real}")


if __name__ == "__main__":
    main()
