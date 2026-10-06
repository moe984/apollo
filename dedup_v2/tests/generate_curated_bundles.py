"""
Generate a controlled STIX bundle set that streams to exactly:
    30 NEW  (unique parent families)
    20 DUPLICATE
    10 SIMILAR

Children are distributed UNEVENLY across parents (see CLUSTER_PLAN) so clusters
have a realistic spread of sizes — one parent with 5 duplicates, one with a mix
of duplicates + a similar, some 2s, some 1s, and several pure-NEW parents with
no children at all.

Names/IDs are realistic (random nanoid alert_ids, real-looking rule names) so
nothing in the labels reveals parent/dup/similar — verdicts and the parent<->child
links are driven entirely by rule name + IOC content, as the engine decides.

Verdict mechanics (engine is NOT modified):
  - DUPLICATE (Layer 1): same rule name + same time_bucket + IOC confidence >= 0.80
  - SIMILAR   (Layer 2): different rule name, same time_bucket, fuzzy IOC score >= 80
  - NEW       (Layer 0): no match

Multi-child correctness:
  - All duplicates of a parent are exact copies (rule + IOCs), so each scores 1.0
    against the parent and every earlier sibling; ties break to the earliest
    ingestion time, which is always the parent -> every duplicate points to parent.
  - Similar siblings share the parent's file_hash and use a near-variant IP chosen
    so the parent is always the *best* fuzzy match (ratio-to-parent > ratio-to-sibling)
    -> every similar points to the parent, not to another similar.
"""

import json
import os
import random
import shutil
import uuid

from rapidfuzz import fuzz

random.seed(1337)  # deterministic output

OUT_DIR = os.path.join(os.path.dirname(__file__), "stix_bundles")

SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
EXT_KEY = "extension-definition--a85759ca-e4a9-43be-98b9-77f3f1fc7686"

# (duplicates, similars) per parent family index. Sums: 20 dups, 10 similars.
# Clusters are homogeneous (all-dup OR all-sim): a parent's exact duplicates are
# content-identical to it, so a similar sharing the cluster would tie across
# parent+dups and the engine's Layer-2 tie-break (empty candidate timestamps)
# can't deterministically attribute it to the parent. Keeping clusters
# homogeneous makes every child's parent unambiguous by score.
CLUSTER_PLAN = {
    0: (5, 0),          # big duplicate cluster
    1: (3, 0),
    2: (2, 0),
    3: (2, 0),
    4: (1, 0), 5: (1, 0), 6: (1, 0), 7: (1, 0),
    8: (1, 0), 9: (1, 0), 10: (1, 0), 11: (1, 0),
    12: (0, 3),         # big similar cluster
    13: (0, 2),
    14: (0, 1), 15: (0, 1), 16: (0, 1), 17: (0, 1), 18: (0, 1),
    # 19..29 -> (0, 0): pure NEW parents, no children
}
N_FAMILIES = 30

RULE_NAMES = [
    "LSU - Windows - Basic Brute Force Detection",
    "SUBR - O365 - Multiple User Creations Within Short Time",
    "ULM - Linux - Unauthorized User Attempted a Sudo Command",
    "LaTech - Palo Alto - High and Critical Threat Allowed",
    "MCNEESE - Defender - New High Severity Alerts",
    "NICHOLLS - Crowdstrike - Intel Detection Allowed",
    "LOSFA - Crowdstrike - On-Demand Scan ML File Analysis",
    "BRCC - Defender - Incident Outbreak Email Removed",
    "ULL - Azure - Impossible Travel Detected",
    "GRAM - Windows - Multiple Account Lockouts for User",
    "NSULA - Defender - Leaked Credentials Detected",
    "SULC - Windows - Basic Brute Force Detection - Admin",
    "RPCC - Defender - Student Mass Phishing Detection",
    "LADELTA - Crowdstrike - Attacker Methodology Detected",
    "PBRC - Crowdstrike - EDR Based Malware Detection",
    "LSU - O365 - Suspicious Rights Delegation",
    "SUBR - Defender - Malicious File Removed After Delivery",
    "ULM - OneLogin - Failed OTP Challenge",
    "LaTech - Windows - Domain Admin Account Sign In",
    "MCNEESE - Azure - Failed Logins Outside of USA",
    "NICHOLLS - Defender - Password Spray Detected",
    "LOSFA - Zscaler - Suspicious Category Blocked High Count",
    "BRCC - Windows - Logs Deleted or Logging Disabled",
    "ULL - Defender - Anonymous IP Address Sign In",
    "GRAM - O365 - Suspicious Rights Delegation",
    "NSULA - Windows - Administrative Action by Non-IAM User",
    "SULC - Umbrella - Suspicious Category Allowed",
    "RPCC - Azure - Unfamiliar Sign-in Properties",
    "LADELTA - Defender - Potential User Account Compromise",
    "PBRC - Palo Alto - Critical Threat Allowed Inbound",
]

# Product-neutral detection suffixes for a "different rule that fired on the same
# asset" — reused with the parent's own "CLIENT - Product" prefix.
NEUTRAL_SUFFIXES = [
    "Correlated Suspicious Behavior",
    "Related Threat Indicator Match",
    "Anomalous Activity on Same Asset",
    "Repeat Offender Activity Detected",
    "Associated IOC Sighting",
]

_ID_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _nanoid(n: int = 21) -> str:
    return "".join(random.choice(_ID_ALPHABET) for _ in range(n))


def _rand_hash() -> str:
    return "".join(random.choice("0123456789abcdef") for _ in range(64))


def _rand_ip() -> str:
    return ".".join(str(random.randint(1, 254)) for _ in range(4))


def _near_ip(parent_ip: str, siblings: list[str], used_ips: set[str],
             lo: float = 66.0, hi: float = 92.0) -> str:
    """A near-variant of parent_ip whose fuzzy ratio to the parent is in [lo,hi]
    AND strictly higher than to any sibling similar — so the parent always wins
    as the best Layer-2 match."""
    for _ in range(20000):
        cand = _rand_ip()
        if cand == parent_ip or cand in used_ips:
            continue
        rp = fuzz.token_sort_ratio(parent_ip, cand)
        if not (lo <= rp <= hi):
            continue
        if all(fuzz.token_sort_ratio(cand, s) < rp for s in siblings):
            return cand
    raise SystemExit(f"could not find a near-IP for {parent_ip} satisfying constraints")


def _alt_names(parent_name: str, n: int, used: set[str]) -> list[str]:
    """n distinct 'different rule' names reusing the parent's CLIENT - Product prefix."""
    parts = parent_name.split(" - ")
    prefix = " - ".join(parts[:2]) if len(parts) >= 2 else parts[0]
    out = []
    for suf in NEUTRAL_SUFFIXES:
        if len(out) >= n:
            break
        cand = f"{prefix} - {suf}"
        if cand == parent_name or cand in used:
            continue
        out.append(cand)
        used.add(cand)
    if len(out) < n:
        raise SystemExit(f"not enough neutral suffixes for {parent_name} (need {n})")
    return out


def _uid(prefix: str) -> str:
    return f"{prefix}--{uuid.uuid4()}"


def build_families() -> list[dict]:
    families = []
    used_ips: set[str] = set()
    used_hashes: set[str] = set()
    for i in range(N_FAMILIES):
        ip = _rand_ip()
        while ip in used_ips:
            ip = _rand_ip()
        used_ips.add(ip)
        h = _rand_hash()
        while h in used_hashes:
            h = _rand_hash()
        used_hashes.add(h)
        families.append({
            "idx": i, "name": RULE_NAMES[i], "ip": ip,
            "file_hash": h, "severity": SEVERITIES[i % len(SEVERITIES)],
        })
    return families


def _validate_distinct(families: list[dict], threshold: float = 80.0) -> None:
    worst = 0.0
    for a in range(len(families)):
        for b in range(a + 1, len(families)):
            fa, fb = families[a], families[b]
            avg = (fuzz.token_sort_ratio(fa["ip"], fb["ip"]) +
                   fuzz.token_sort_ratio(fa["file_hash"], fb["file_hash"])) / 2
            worst = max(worst, avg)
            if avg >= threshold:
                raise SystemExit(f"Families {fa['idx']}/{fb['idx']} too similar ({avg:.1f}).")
    if len(set(f["name"] for f in families)) != len(families):
        raise SystemExit("Duplicate parent rule names.")
    print(f"[validate] worst cross-family fuzzy avg = {worst:.1f} (< {threshold}); 30 distinct rule names OK")


def build_bundle(name: str, ip: str, file_hash: str, created: str) -> dict:
    return {
        "id": _uid("bundle"), "type": "bundle",
        "objects": [
            {"id": _uid("identity"), "name": "Splunk", "type": "identity",
             "created": created, "modified": created,
             "description": "Splunk app: SplunkSOAR",
             "spec_version": "2.1", "identity_class": "system"},
            {"id": _uid("indicator"), "name": name, "type": "indicator",
             "created": created, "modified": created,
             "pattern": f"[ipv4-addr:value = '{ip}']", "pattern_type": "stix",
             "spec_version": "2.1", "valid_from": created,
             "indicator_types": ["anomalous-activity"],
             "extensions": {EXT_KEY: {"extension_type": "property-extension",
                                      "apollo_severity": "MEDIUM"}}},
            {"id": _uid("ipv4-addr"), "type": "ipv4-addr", "value": ip},
            {"id": _uid("file"), "type": "file", "hashes": {"SHA-256": file_hash}},
        ],
    }


def envelope(alert_id, severity, created_at, bundle, status):
    return {"alert_id": alert_id, "source": "splunk", "severity": severity,
            "status": status, "created_at": created_at, "stix_bundle": bundle}


def main() -> None:
    families = build_families()
    _validate_distinct(families)

    if os.path.isdir(OUT_DIR):
        shutil.rmtree(OUT_DIR)
    os.makedirs(OUT_DIR)

    records = []       # (created_at, alert_id, envelope)
    ledger = []        # (intent, alert_id, parent_id, rule)
    used_alt_names: set[str] = set(RULE_NAMES)
    used_ips = {f["ip"] for f in families}
    child_clock = 0    # children all stream after parents (13:MM:SS)

    def child_ts() -> str:
        nonlocal child_clock
        ts = f"2026-07-10T13:{child_clock // 60:02d}:{child_clock % 60:02d}.000Z"
        child_clock += 1
        return ts

    # Parents first (unique rule + IOCs -> NEW), earliest timestamps.
    parent_ids = {}
    for f in families:
        created = f"2026-07-10T12:00:{f['idx']:02d}.000Z"
        aid = _nanoid()
        parent_ids[f["idx"]] = aid
        records.append((created, aid, envelope(aid, f["severity"], created,
                        build_bundle(f["name"], f["ip"], f["file_hash"], created), "queued")))
        ledger.append(("PARENT/NEW", aid, "-", f["name"]))

    # Children per CLUSTER_PLAN.
    for f in families:
        dups, sims = CLUSTER_PLAN.get(f["idx"], (0, 0))
        pid = parent_ids[f["idx"]]

        for _ in range(dups):   # exact copies -> DUPLICATE, all match the parent
            created = child_ts()
            aid = _nanoid()
            records.append((created, aid, envelope(aid, f["severity"], created,
                            build_bundle(f["name"], f["ip"], f["file_hash"], created), "manual_review")))
            ledger.append(("DUPLICATE", aid, pid, f["name"]))

        if sims:
            alt_names = _alt_names(f["name"], sims, used_alt_names)
            sib_ips: list[str] = []
            for s in range(sims):   # different rule + shared hash + near IP -> SIMILAR
                created = child_ts()
                aid = _nanoid()
                near = _near_ip(f["ip"], sib_ips, used_ips)
                used_ips.add(near)
                sib_ips.append(near)
                exp = (fuzz.token_sort_ratio(f["ip"], near) + 100) / 2
                records.append((created, aid, envelope(aid, f["severity"], created,
                                build_bundle(alt_names[s], near, f["file_hash"], created), "queued")))
                ledger.append((f"SIMILAR(~{exp:.0f}%)", aid, pid, alt_names[s]))

    for created, aid, env in records:
        with open(os.path.join(OUT_DIR, f"{aid}.json"), "w", encoding="utf-8") as fh:
            json.dump(env, fh, indent=2)

    n_dups = sum(d for d, _ in CLUSTER_PLAN.values())
    n_sims = sum(s for _, s in CLUSTER_PLAN.values())
    print(f"[write] {len(records)} bundles -> {OUT_DIR}")
    print(f"        parents={N_FAMILIES}  dups={n_dups}  similars={n_sims}")
    sizes = {}
    for idx, (d, s) in CLUSTER_PLAN.items():
        sizes[d + s] = sizes.get(d + s, 0) + 1
    sizes[0] = sizes.get(0, 0) + (N_FAMILIES - len(CLUSTER_PLAN))
    print(f"        cluster sizes (children per parent): {dict(sorted(sizes.items()))}")
    print("Expected stream verdicts: NEW=30  DUPLICATE=20  SIMILAR=10")

    ledger_path = os.path.join(os.path.dirname(__file__), "curated_ledger.tsv")
    with open(ledger_path, "w", encoding="utf-8") as fh:
        fh.write("intent\talert_id\tparent_id\trule_name\n")
        for row in ledger:
            fh.write("\t".join(row) + "\n")
    print(f"[ledger] intended parent<->child mapping written to {ledger_path}")


if __name__ == "__main__":
    main()
