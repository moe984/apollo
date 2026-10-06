#!/usr/bin/env python3
"""Send a controlled dedup mix to Apollo DEV: 1 NEW, 5 DUPLICATE, 3 SIMILAR.

The stock run.sh derives all IOCs from the sdi, so it can only make NEW /
DUPLICATE. SIMILAR needs PARTIAL IOC overlap. The CIP engine uses 4 IOC slots
(source_ip, dest_ip, user, file_hash), layer-1 threshold 0.80, layer-2 fuzzy
threshold 80, 30-min window:
  - DUPLICATE: all 4 slots identical (3/4 = 0.75 < 0.80, so must be 4/4).
  - SIMILAR:   3 slots identical + 1 near-variant -> 3/4 exact (misses L1) and
               fuzzy avg ~98 (>= 80, hits L2), same time bucket.

Reuses build_event/post from ../prod/soar_gate_test.py.
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
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def verdict_of(body):
    if not isinstance(body, dict):
        return f"<non-json: {body}>"
    return (
        f"{body.get('verdict')} "
        f"(match={body.get('match_type')}, "
        f"alert={body.get('apollo_alert_id')}, "
        f"canonical={body.get('canonical_alert') or body.get('parent_alert_id') or '-'})"
    )


def main():
    env = load_env(os.path.join(HERE, ".env"))
    url = env["APOLLO_URL"]
    api_key = env["APOLLO_API_KEY"]
    customer_id = env.get("APOLLO_CUSTOMER_ID", "")
    endpoint = f"{url}{INGEST_PATH}"
    timeout = 30.0

    rule = "PBRC - Palo Alto - Critical Threat Allowed Inbound"
    sev = "high"
    # SIMILAR (layer 2) requires a DIFFERENT alert_name than the match candidate
    # (layer2.py skips same-rule candidates — same rule is layer-1's job) AND
    # fuzzy IOC score >= 80 in the same time bucket. So similars reuse the
    # canonical's IOCs (fuzzy = 100) but carry a distinct rule name each.
    SIMILAR_RULES = [
        "PBRC - Palo Alto - Suspicious Outbound Connection",
        "PBRC - Fortinet - Anomalous Session Volume",
        "PBRC - CrowdStrike - Credential Access Attempt",
    ]

    # Canonical IOCs (the "New"/unique alert). All others reference these.
    C_IP = "203.0.113.10"
    C_DST = "198.51.100.20"
    C_HASH = hashlib.sha256(b"mix-canonical-2026-07-20").hexdigest()
    C_USER = "e2e-mix@corp.local"

    plan = []
    # 1) NEW — the canonical. Must be sent (and stored) first.
    plan.append(("NEW", 990100, "mix-new-1", C_IP, C_DST, C_HASH, C_USER, rule))
    # 2) 5 DUPLICATE — identical IOCs + same rule, new container/sdi (L1 4/4).
    for i in range(1, 6):
        plan.append(("DUPLICATE", 990100 + i, f"mix-dup-{i}", C_IP, C_DST, C_HASH, C_USER, rule))
    # 3) 3 SIMILAR — same IOCs, DIFFERENT rule each (L1 misses on alert_name
    #    prerequisite; L2 fuzzy = 100 since rule differs -> SIMILAR).
    for i in range(1, 4):
        plan.append(("SIMILAR", 990200 + i, f"mix-sim-{i}", C_IP, C_DST, C_HASH, C_USER, SIMILAR_RULES[i - 1]))

    results = {"NEW": 0, "DUPLICATE": 0, "SIMILAR": 0, "OTHER": 0}
    print(f"POST {endpoint}  customer={customer_id}\n")
    for expected, cid_num, sdi, ip, dst, fh, user, ev_rule in plan:
        event = build_event(sdi, ip=ip, dst_ip=dst, file_hash=fh, user=user,
                            rule=ev_rule, severity=sev, container_id=cid_num)
        status, body, ms = post(endpoint, api_key, customer_id, event, timeout)
        got = body.get("verdict") if isinstance(body, dict) else None
        ok = "OK " if got == expected else "!! "
        results[got if got in results else "OTHER"] += 1
        print(f"{ok}[expect {expected:9}] HTTP {status} {ms:6.0f}ms  -> {verdict_of(body)}")

    print("\n=== TOTALS ===")
    for k in ("NEW", "DUPLICATE", "SIMILAR", "OTHER"):
        print(f"  {k}: {results[k]}")
    target = {"NEW": 1, "DUPLICATE": 5, "SIMILAR": 3, "OTHER": 0}
    print(f"\nTarget: NEW=1 DUPLICATE=5 SIMILAR=3")
    print("RESULT:", "PASS" if results == target else "MISMATCH")


if __name__ == "__main__":
    main()
