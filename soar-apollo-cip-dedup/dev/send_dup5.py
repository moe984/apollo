#!/usr/bin/env python3
"""Send a duplicate group to Apollo DEV with ALL 5 reachable IOC slots filled.

The SOAR CEF->STIX adapter (cef-to-alert.ts -> splunk.adapter.ts) builds
observables for ip / user / file_hash / domain / url. It does NOT build an
email-addr SCO, so the engine's 6th slot (email) cannot be filled through this
ingest path without an adapter change.

Group: 1 canonical (NEW) + 4 exact duplicates (same IOCs + same rule). Each
event fills all 5 slots identically, so Layer 1 should match 5/5 and every
follow-on lands DUPLICATE (match=exact_hash).

    1 canonical + 4 dups  ->  1 NEW + 4 DUPLICATE
"""
import os
import sys
import hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "prod"))
from soar_gate_test import post, INGEST_PATH  # noqa: E402


def load_env(path):
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def build_event_5(sdi, *, ip, dst_ip, file_hash, user, domain, url,
                  rule, severity, container_id):
    """SOAR container carrying all 5 slot-filling CEF observables."""
    return {
        "container": {
            "id": container_id,
            "name": rule,
            "severity": severity,
            "source_data_identifier": sdi,
        },
        "artifacts": [
            {
                "label": "artifact",
                "name": "network-observable",
                "cef": {
                    "sourceAddress": ip,
                    "destinationAddress": dst_ip,
                    "fileHashSha256": file_hash,
                    "destinationUserName": user,
                    "destinationHostName": domain,
                    "requestURL": url,
                },
            }
        ],
    }


def verdict_line(body):
    if not isinstance(body, dict):
        return f"<non-json: {body}>"
    return (f"{body.get('verdict')} (match={body.get('match_type')}, "
            f"alert={body.get('apollo_alert_id')})")


def main():
    env = load_env(os.path.join(HERE, ".env"))
    url_base, api_key = env["APOLLO_URL"], env["APOLLO_API_KEY"]
    customer_id = env.get("APOLLO_CUSTOMER_ID", "")
    endpoint = f"{url_base}{INGEST_PATH}"
    sev, timeout = "high", 30.0

    # One self-consistent IOC set, filling all 5 reachable slots.
    h = hashlib.sha256(b"dup5-group").hexdigest()
    iocs = dict(
        ip="203.0.113.42",
        dst_ip="198.51.100.42",
        file_hash=h,
        user="e2e-dup5@corp.local",
        domain="malicious-c2.example.net",
        url="https://malicious-c2.example.net/beacon?id=42",
    )
    rule = "PBRC - Palo Alto - Critical Threat Allowed Inbound"

    plan = [
        ("NEW",       992010, "scn-dup5-canon"),
        ("DUPLICATE", 992011, "scn-dup5-1"),
        ("DUPLICATE", 992012, "scn-dup5-2"),
        ("DUPLICATE", 992013, "scn-dup5-3"),
        ("DUPLICATE", 992014, "scn-dup5-4"),
    ]

    results = {"NEW": 0, "DUPLICATE": 0, "SIMILAR": 0, "OTHER": 0}
    print(f"POST {endpoint}  customer={customer_id}")
    print(f"slots: ip,dst_ip,user,file_hash,domain,url  (email not reachable via SOAR)\n")
    for expected, cid, sdi in plan:
        event = build_event_5(sdi, rule=rule, severity=sev, container_id=cid, **iocs)
        status, body, ms = post(endpoint, api_key, customer_id, event, timeout)
        got = body.get("verdict") if isinstance(body, dict) else None
        ok = "OK " if got == expected else "!! "
        results[got if got in results else "OTHER"] += 1
        print(f"{ok}[expect {expected:9}] HTTP {status} {ms:6.0f}ms -> {verdict_line(body)}")

    print("\n=== TOTALS ===")
    for k in ("NEW", "DUPLICATE", "SIMILAR", "OTHER"):
        print(f"  {k}: {results[k]}")
    target = {"NEW": 1, "DUPLICATE": 4, "SIMILAR": 0, "OTHER": 0}
    print("\nTarget: NEW=1 DUPLICATE=4")
    print("RESULT:", "PASS" if results == target else "MISMATCH")


if __name__ == "__main__":
    main()
