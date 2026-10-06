#!/usr/bin/env python3
"""Send a dedup scenario to Apollo DEV and show how verdicts actually land.

Intent: "3 duplicates, 5 similars, 2 unique". But the FIRST alert of any dedup
group is always the canonical (NEW) — only later arrivals can be Duplicate or
Similar. So the queue should show:

    2 unique                     -> 2 NEW
    dup group  (1 canon + 2 dup) -> 1 NEW + 2 DUPLICATE
    sim group  (1 canon + 4 sim) -> 1 NEW + 4 SIMILAR
    ---------------------------------------------------
    TOTAL: 4 NEW, 2 DUPLICATE, 4 SIMILAR   (10 alerts)

Engine rules (4 IOC slots: source_ip/dest_ip/user/file_hash, L1>=0.80, L2 fuzzy
>=80, 30-min window):
  - DUPLICATE: all 4 slots identical + SAME rule (alert_name)  -> L1 4/4.
  - SIMILAR:   same IOCs but a DIFFERENT rule each             -> L1 misses on
               alert_name prerequisite; L2 fuzzy = 100.
"""
import os
import sys
import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "prod"))
from soar_gate_test import build_event, post, INGEST_PATH  # noqa: E402


def load_env(path):
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def iocset(seed):
    """A distinct, self-consistent IOC set derived from a seed."""
    h = hashlib.sha256(seed.encode()).hexdigest()
    o1, o2 = int(h[0:2], 16) % 254 + 1, int(h[2:4], 16) % 254 + 1
    return dict(
        ip=f"203.0.{o1}.{o2}",
        dst_ip=f"198.51.{o2}.{o1}",
        file_hash=h,
        user=f"e2e-{h[:8]}@corp.local",
    )


def verdict_line(body):
    if not isinstance(body, dict):
        return f"<non-json: {body}>"
    return (f"{body.get('verdict')} (match={body.get('match_type')}, "
            f"alert={body.get('apollo_alert_id')}, "
            f"canonical={body.get('canonical_alert') or body.get('parent_alert_id') or '-'})")


def main():
    env = load_env(os.path.join(HERE, ".env"))
    url, api_key = env["APOLLO_URL"], env["APOLLO_API_KEY"]
    customer_id = env.get("APOLLO_CUSTOMER_ID", "")
    endpoint = f"{url}{INGEST_PATH}"
    sev, timeout = "high", 30.0

    IA = iocset("scenario-dup-group")     # duplicate group's IOCs
    IB = iocset("scenario-sim-group")     # similar group's IOCs
    U1 = iocset("scenario-unique-one")
    U2 = iocset("scenario-unique-two")

    RA = "PBRC - Palo Alto - Critical Threat Allowed Inbound"   # dup group rule
    RB0 = "PBRC - Fortinet - Baseline Session"                  # sim canonical rule
    SIM_RULES = [
        "PBRC - Fortinet - Suspicious Outbound Connection",
        "PBRC - CrowdStrike - Credential Access Attempt",
        "PBRC - Palo Alto - Anomalous Session Volume",
        "PBRC - Zscaler - Possible Data Exfiltration",
    ]

    # (expected, container_id, sdi, iocs, rule)
    plan = [
        # 2 totally unique -> NEW, NEW
        ("NEW", 991001, "scn-uniq-1", U1, "PBRC - Unique Alpha"),
        ("NEW", 991002, "scn-uniq-2", U2, "PBRC - Unique Beta"),
        # duplicate group: canonical first, then 3 exact dups
        ("NEW",       991010, "scn-dup-canon", IA, RA),
        ("DUPLICATE", 991011, "scn-dup-1",     IA, RA),
        ("DUPLICATE", 991012, "scn-dup-2",     IA, RA),
        ("DUPLICATE", 991013, "scn-dup-3",     IA, RA),
        # similar group: canonical first, then 4 similars (same IOCs, diff rule)
        ("NEW",     991020, "scn-sim-canon", IB, RB0),
        ("SIMILAR", 991021, "scn-sim-1",     IB, SIM_RULES[0]),
        ("SIMILAR", 991022, "scn-sim-2",     IB, SIM_RULES[1]),
        ("SIMILAR", 991023, "scn-sim-3",     IB, SIM_RULES[2]),
        ("SIMILAR", 991024, "scn-sim-4",     IB, SIM_RULES[3]),
    ]

    results = {"NEW": 0, "DUPLICATE": 0, "SIMILAR": 0, "OTHER": 0}
    print(f"POST {endpoint}  customer={customer_id}\n")
    for expected, cid, sdi, iocs, rule in plan:
        event = build_event(sdi, rule=rule, severity=sev, container_id=cid, **iocs)
        status, body, ms = post(endpoint, api_key, customer_id, event, timeout)
        got = body.get("verdict") if isinstance(body, dict) else None
        ok = "OK " if got == expected else "!! "
        results[got if got in results else "OTHER"] += 1
        print(f"{ok}[expect {expected:9}] HTTP {status} {ms:6.0f}ms -> {verdict_line(body)}")

    print("\n=== TOTALS ===")
    for k in ("NEW", "DUPLICATE", "SIMILAR", "OTHER"):
        print(f"  {k}: {results[k]}")
    target = {"NEW": 4, "DUPLICATE": 3, "SIMILAR": 4, "OTHER": 0}
    print("\nTarget: NEW=4 DUPLICATE=3 SIMILAR=4")
    print("RESULT:", "PASS" if results == target else "MISMATCH")


if __name__ == "__main__":
    main()
