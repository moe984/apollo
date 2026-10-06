"""
Hash Dedup False Positive Auditor

Reads dedup_input.csv, finds all hash-deduplicated pairs, compares
what the hash saw vs what's actually inside the alerts, and flags
potential false positives.

Output: audit_report.json + audit_report.txt in dedup/data/

Usage:
    python audit_fp.py
"""

import csv
import json
import os
import re
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "..", "data")


def parse_splunk_raw(raw_payload: dict) -> dict:
    """Extract fields from Splunk _raw string."""
    raw_str = raw_payload.get("_raw", "")
    fields = {}
    for match in re.finditer(r'(\w+)="([^"]*)"|(\w+)=(\S+)', raw_str):
        k = match.group(1) or match.group(3)
        v = match.group(2) or match.group(4)
        fields[k] = v
    # Also grab top-level fields
    for k, v in raw_payload.items():
        if isinstance(v, str) and k not in fields:
            fields[k] = v
    return fields


def extract_stix_iocs(stix_bundle: dict) -> list[dict]:
    """Extract IOCs from STIX bundle."""
    iocs = []
    for obj in stix_bundle.get("objects", []):
        t = obj.get("type", "")
        if t in ("ipv4-addr", "ipv6-addr"):
            iocs.append({"type": "ip", "value": obj.get("value", "")})
        elif t == "domain-name":
            iocs.append({"type": "domain", "value": obj.get("value", "")})
        elif t == "user-account":
            if obj.get("user_id"):
                iocs.append({"type": "user", "value": obj["user_id"]})
            if obj.get("account_login") and obj.get("account_login") != obj.get("user_id"):
                iocs.append({"type": "user", "value": obj["account_login"]})
        elif t == "file" and obj.get("hashes"):
            for algo, val in obj["hashes"].items():
                iocs.append({"type": "file_hash", "value": val, "algo": algo})
        elif t == "url":
            iocs.append({"type": "url", "value": obj.get("value", "")})
        elif t == "email-addr":
            iocs.append({"type": "email", "value": obj.get("value", "")})
    return iocs


def compute_hash_quality(fields: dict) -> dict:
    """Compute hash quality score — how many components are non-empty."""
    src_ip = fields.get("src_ip", fields.get("src", ""))
    dest_ip = fields.get("dest_ip", fields.get("dest", ""))
    ioc = fields.get("IOC", "")
    ioc_type = fields.get("IOC_Type", "")
    search_name = fields.get("search_name", fields.get("rule_name", ""))

    has_src = bool(src_ip or (ioc and ioc_type == "IP"))
    has_dest = bool(dest_ip)
    has_category = bool(search_name)

    filled = sum([has_src, has_dest, has_category])
    total = 3  # src, dest, category (time_bucket always present)

    return {
        "score": filled,
        "max": total,
        "rating": ["EMPTY", "WEAK", "MODERATE", "STRONG"][filled],
        "has_src_ip": has_src,
        "has_dest_ip": has_dest,
        "has_category": has_category,
        "src_ip": src_ip or (ioc if ioc_type == "IP" else ""),
        "dest_ip": dest_ip,
        "category": search_name,
        "ioc": ioc,
        "ioc_type": ioc_type,
    }


def diff_iocs(iocs_a: list[dict], iocs_b: list[dict]) -> dict:
    """Compare IOC sets between two alerts."""
    set_a = {(i["type"], i["value"]) for i in iocs_a}
    set_b = {(i["type"], i["value"]) for i in iocs_b}

    shared = set_a & set_b
    only_a = set_a - set_b
    only_b = set_b - set_a

    return {
        "shared": [{"type": t, "value": v} for t, v in sorted(shared)],
        "only_canonical": [{"type": t, "value": v} for t, v in sorted(only_a)],
        "only_duplicate": [{"type": t, "value": v} for t, v in sorted(only_b)],
        "shared_count": len(shared),
        "diff_count": len(only_a) + len(only_b),
        "similarity": len(shared) / max(len(set_a | set_b), 1),
    }


def main():
    # Load alerts
    input_path = os.path.join(DATA_DIR, "dedup_input.csv")
    if not os.path.exists(input_path):
        print(f"ERROR: {input_path} not found. Run fetch_dedup_data.py first.")
        return

    with open(input_path) as f:
        rows = {r["id"]: r for r in csv.DictReader(f)}

    # Find hash-deduplicated pairs
    pairs = []
    for r in rows.values():
        if r["parent_alert_id"] and r["parent_alert_id"] in rows:
            try:
                meta = json.loads(r["dedup_metadata"]) if r["dedup_metadata"] else {}
            except (json.JSONDecodeError, TypeError):
                meta = {}
            if meta.get("dedup_method") == "hash":
                pairs.append({
                    "duplicate_id": r["id"],
                    "canonical_id": r["parent_alert_id"],
                })

    print(f"Found {len(pairs)} hash-deduplicated pairs")

    # Analyze each pair
    results = []
    fp_count = 0
    for pair in pairs:
        dup = rows[pair["duplicate_id"]]
        can = rows[pair["canonical_id"]]

        # Parse payloads
        dup_raw = json.loads(dup["raw_payload"]) if dup["raw_payload"] else {}
        can_raw = json.loads(can["raw_payload"]) if can["raw_payload"] else {}
        dup_stix = json.loads(dup["stix_bundle"]) if dup["stix_bundle"] else {}
        can_stix = json.loads(can["stix_bundle"]) if can["stix_bundle"] else {}

        # Extract fields
        dup_fields = parse_splunk_raw(dup_raw)
        can_fields = parse_splunk_raw(can_raw)

        # Hash quality
        dup_quality = compute_hash_quality(dup_fields)
        can_quality = compute_hash_quality(can_fields)

        # STIX IOCs
        dup_iocs = extract_stix_iocs(dup_stix)
        can_iocs = extract_stix_iocs(can_stix)

        # Diff
        ioc_diff = diff_iocs(can_iocs, dup_iocs)

        # Is it a test alert?
        dup_meta = json.loads(dup["apollo_metadata"]) if dup["apollo_metadata"] else {}
        can_meta = json.loads(can["apollo_metadata"]) if can["apollo_metadata"] else {}
        is_test = (
            str(dup_meta.get("sourceAlertId", "")).startswith("dedup-test") or
            str(can_meta.get("sourceAlertId", "")).startswith("dedup-test")
        )

        # Time gap
        try:
            t1 = datetime.fromisoformat(can["created_at"].strip('"').replace("Z", "+00:00"))
            t2 = datetime.fromisoformat(dup["created_at"].strip('"').replace("Z", "+00:00"))
            gap_seconds = abs((t2 - t1).total_seconds())
        except Exception:
            gap_seconds = -1

        # Determine FP risk
        if is_test:
            fp_verdict = "TEST_ALERT"
        elif ioc_diff["diff_count"] > 0 and ioc_diff["similarity"] < 0.5:
            fp_verdict = "LIKELY_FALSE_POSITIVE"
            fp_count += 1
        elif ioc_diff["diff_count"] > 0:
            fp_verdict = "POSSIBLE_FALSE_POSITIVE"
            fp_count += 1
        elif can_quality["score"] <= 1:
            fp_verdict = "WEAK_HASH_RISK"
        else:
            fp_verdict = "LIKELY_TRUE_POSITIVE"

        result = {
            "duplicate_id": pair["duplicate_id"],
            "canonical_id": pair["canonical_id"],
            "gap_seconds": round(gap_seconds, 1),
            "is_test_alert": is_test,
            "fp_verdict": fp_verdict,
            "hash_quality": {
                "canonical": can_quality,
                "duplicate": dup_quality,
            },
            "ioc_diff": ioc_diff,
            "canonical": {
                "severity": can["severity"],
                "search_name": can_fields.get("search_name", ""),
                "title": can_fields.get("title", ""),
                "ioc": can_fields.get("IOC", ""),
                "ioc_type": can_fields.get("IOC_Type", ""),
                "accountName": can_fields.get("accountName", ""),
                "sourceAlertId": can_meta.get("sourceAlertId", ""),
            },
            "duplicate": {
                "severity": dup["severity"],
                "search_name": dup_fields.get("search_name", ""),
                "title": dup_fields.get("title", ""),
                "ioc": dup_fields.get("IOC", ""),
                "ioc_type": dup_fields.get("IOC_Type", ""),
                "accountName": dup_fields.get("accountName", ""),
                "sourceAlertId": dup_meta.get("sourceAlertId", ""),
            },
        }
        results.append(result)

    # Sort: FPs first, then by gap
    priority = {"LIKELY_FALSE_POSITIVE": 0, "POSSIBLE_FALSE_POSITIVE": 1, "WEAK_HASH_RISK": 2, "TEST_ALERT": 3, "LIKELY_TRUE_POSITIVE": 4}
    results.sort(key=lambda r: (priority.get(r["fp_verdict"], 5), -r["gap_seconds"]))

    # Write JSON report
    json_path = os.path.join(DATA_DIR, "audit_report.json")
    with open(json_path, "w") as f:
        json.dump({"total_pairs": len(results), "false_positives": fp_count, "pairs": results}, f, indent=2)

    # Write human-readable report
    txt_path = os.path.join(DATA_DIR, "audit_report.txt")
    with open(txt_path, "w") as f:
        f.write("HASH DEDUP FALSE POSITIVE AUDIT\n")
        f.write(f"Generated: {datetime.utcnow().isoformat()}Z\n")
        f.write(f"Total pairs: {len(results)}\n")
        f.write(f"Flagged:     {fp_count}\n")
        f.write("=" * 100 + "\n\n")

        for r in results:
            verdict = r["fp_verdict"]
            marker = {"LIKELY_FALSE_POSITIVE": "!!! FP", "POSSIBLE_FALSE_POSITIVE": "!! FP?", "WEAK_HASH_RISK": "! WEAK", "TEST_ALERT": "  TEST", "LIKELY_TRUE_POSITIVE": "  OK"}
            f.write(f"[{marker.get(verdict, '?'):>6}] {r['canonical_id'][:20]} <- {r['duplicate_id'][:20]}  gap={r['gap_seconds']}s\n")
            f.write(f"        hash quality: canonical={r['hash_quality']['canonical']['rating']} duplicate={r['hash_quality']['duplicate']['rating']}\n")

            # Show what hash saw
            cq = r["hash_quality"]["canonical"]
            f.write(f"        hash input:   src={cq['src_ip'] or '(empty)'}  dest={cq['dest_ip'] or '(empty)'}  cat={cq['category'][:50]}\n")

            # Show what's actually different
            if r["canonical"]["title"] != r["duplicate"]["title"]:
                f.write(f"        DIFF title:   '{r['canonical']['title'][:40]}' vs '{r['duplicate']['title'][:40]}'\n")
            if r["canonical"]["ioc"] != r["duplicate"]["ioc"]:
                f.write(f"        DIFF ioc:     '{r['canonical']['ioc'][:40]}' vs '{r['duplicate']['ioc'][:40]}'\n")
            if r["canonical"]["accountName"] != r["duplicate"]["accountName"]:
                f.write(f"        DIFF account: '{r['canonical']['accountName']}' vs '{r['duplicate']['accountName']}'\n")

            diff = r["ioc_diff"]
            if diff["only_canonical"]:
                f.write(f"        IOCs only in canonical: {json.dumps(diff['only_canonical'][:3])}\n")
            if diff["only_duplicate"]:
                f.write(f"        IOCs only in duplicate: {json.dumps(diff['only_duplicate'][:3])}\n")
            f.write(f"        IOC overlap:  {diff['shared_count']} shared, {diff['diff_count']} different, similarity={diff['similarity']:.0%}\n")
            f.write("\n")

    print(f"\nResults:")
    print(f"  Total pairs analyzed: {len(results)}")
    print(f"  Flagged as FP:       {fp_count}")
    print(f"  Test alerts:         {sum(1 for r in results if r['fp_verdict'] == 'TEST_ALERT')}")
    print(f"  True positives:      {sum(1 for r in results if r['fp_verdict'] == 'LIKELY_TRUE_POSITIVE')}")
    print(f"\nReports saved:")
    print(f"  {txt_path}")
    print(f"  {json_path}")


if __name__ == "__main__":
    main()
