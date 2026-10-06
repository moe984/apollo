#!/usr/bin/env python3
"""Synthetic alert engine for Apollo (prod SOAR ingest path).

ONE script that generates and pushes synthetic SOAR alerts into Apollo through
the real production gate:

    POST /api/v1/ingest/soar   (x-api-key + x-client-name headers)

and reads back the CIP dedup verdict (NEW / DUPLICATE / SIMILAR) that SOAR
would branch on. It replaces the pile of one-off send_* scripts this folder
used to hold.

What it generates (all attributes are covered in every run):

  * 3 clients .......... default LSU, FTCC, MCNEESE (--clients)
  * 4 severities ....... critical, high, medium, low (cycled across alerts)
  * 10 alert types ..... distinct rule names / detections (cycled)
  * 6 IOC slots ........ the CEF fields Apollo maps to STIX observables:
        1. sourceAddress        (source IPv4)      private/reserved, safe
        2. destinationAddress   (dest IPv4)
        3. sourceUserName       (user account)
        4. destinationHostName  (hostname)
        5. fileHashSha256        (SHA-256 file hash)
        6. requestURL           (URL)
  * 3 dedup verdicts, produced deliberately:
        NEW ........ fresh IOC set, unique rule            -> verdict NEW
        DUPLICATE .. identical 6 IOCs + same rule,         -> verdict DUPLICATE
                     new container id/source_data_identifier
        SIMILAR .... same IP + user (+ most slots), ONE    -> verdict SIMILAR
                     slot varied (URL), DIFFERENT rule name  (layer-2 fuzzy)

Tenant note: multi-client seeding requires a `soar_gateway`-scoped API key
(tenant is read from each event). A `tenant`-scoped key ignores the client
name and files EVERYTHING under its own tenant, so all 3 clients collapse to 1.
See ./README.md.

Config: reads APOLLO_URL / APOLLO_API_KEY from ./.env (override with flags).

Usage:
  python3 synthetic_alert_engine.py --dry-run          # build + print, send nothing
  python3 synthetic_alert_engine.py                    # full matrix into prod
  python3 synthetic_alert_engine.py --clients LSU --new 5 --dup 3 --similar 3
  python3 synthetic_alert_engine.py --only new --new 10 --seed 42
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request

ENDPOINT = "/api/v1/ingest/soar"

DEFAULT_CLIENTS = ["LSU", "FTCC", "MCNEESE"]           # 3 clients
SEVERITIES = ["critical", "high", "medium", "low"]     # 4 severities
ALERT_TYPES = [                                        # 10 alert types (rule names)
    "Palo Alto - Critical Threat Allowed Inbound",
    "CrowdStrike - Malware Detected on Endpoint",
    "Microsoft Defender - Suspicious Process Execution",
    "Okta - Impossible Travel Sign-In",
    "Proofpoint - Credential Phishing URL Clicked",
    "SentinelOne - Ransomware Behavior Blocked",
    "Fortinet IPS - Exploit Attempt Blocked",
    "AWS GuardDuty - Anomalous API Activity",
    "Zscaler - Command-and-Control Callback",
    "Splunk - Brute Force Authentication",
]

# ---------------------------------------------------------------------------
# IOC generation (6 slots). Values stay in reserved/private space so no real
# host is ever named: RFC1918 10.0.0.0/8 + 172.16.0.0/12 for IPs, the RFC6761
# reserved `.example` TLD for domains, random synthetic hashes.
#
# Diversity matters: the dedup engine's layer-2 fuzzy match keys on shared
# observable VALUES (registrable domain, subnet). If every NEW alert reused one
# /24 and one registrable domain (the earlier 198.51.100.0/24 + example.net
# recipe), NEW alerts fuzzy-matched EACH OTHER and collapsed to SIMILAR. So each
# alert gets a unique per-alert token that varies the user and BOTH domains
# (corp<tok>.example, badnet<tok>.example) and spreads its IPs across the /8 and
# /12, making a fresh alert genuinely distinct from every other one.
# ---------------------------------------------------------------------------

def gen_iocs(rng: random.Random) -> dict:
    """A fresh, unique 6-slot IOC set (a NEW canonical's observables).

    The per-alert `tok` gives the user + both registrable domains a value no
    other alert shares, so two NEW alerts do not fuzzy-match. DUPLICATE reuses
    this dict verbatim; SIMILAR keeps it and varies only requestURL.
    """
    tok = "%07x" % rng.getrandbits(28)   # unique per alert (~268M space)
    return {
        "sourceAddress": f"10.{rng.randint(1, 254)}.{rng.randint(0, 254)}.{rng.randint(1, 254)}",
        "destinationAddress": f"172.{rng.randint(16, 31)}.{rng.randint(0, 254)}.{rng.randint(1, 254)}",
        "sourceUserName": f"user_{tok}",
        "destinationHostName": f"host-{rng.randint(1, 9999)}.corp{tok}.example",
        "fileHashSha256": "%064x" % rng.getrandbits(256),
        "requestURL": f"http://c2-{rng.randint(100, 999)}.badnet{tok}.example/{rng.randint(10000, 99999)}/payload",
    }


def build_event(client: str, container_id: int, sdi: str, rule: str,
                severity: str, iocs: dict) -> dict:
    """A SOAR-shaped container + artifact carrying all 6 IOC slots.

    Tenant is asserted three ways (CEF ClientName, container.tenant_name, and
    the x-client-name header set in post()) so a soar_gateway key resolves the
    client regardless of which signal the gate reads first.
    """
    cef = {"ClientName": client}
    cef.update(iocs)                       # the 6 IOC slots
    return {
        "container": {
            "id": container_id,
            "name": rule,
            "tenant_name": client,
            "severity": severity,
            "source_data_identifier": sdi,
        },
        "artifacts": [
            {"label": "artifact", "name": "network-observable", "cef": cef}
        ],
    }


# ---------------------------------------------------------------------------
# Plan: build the full list of (kind, expected_verdict, event) tuples for a
# client BEFORE sending, so the mix is deterministic and reviewable in --dry-run.
# ---------------------------------------------------------------------------

def plan_client(client: str, base_cid: int, run_tag: int, rng: random.Random,
                n_new: int, n_dup: int, n_similar: int, only: str | None) -> list:
    plan: list[tuple[str, str, dict]] = []
    seq = 0
    typ = 0  # rotates through the 10 alert types
    sev = 0  # rotates through the 4 severities

    def next_type() -> str:
        nonlocal typ
        t = ALERT_TYPES[typ % len(ALERT_TYPES)]
        typ += 1
        return t

    def next_sev() -> str:
        nonlocal sev
        s = SEVERITIES[sev % len(SEVERITIES)]
        sev += 1
        return s

    def cid() -> int:
        nonlocal seq
        seq += 1
        return base_cid + seq

    # --- NEW: each a fresh canonical with unique IOCs ---
    if only in (None, "new"):
        for i in range(n_new):
            c = cid()
            plan.append(("NEW", "NEW", build_event(
                client, c, f"{client.lower()}-new-{run_tag}-{i}",
                next_type(), next_sev(), gen_iocs(rng))))

    # --- DUPLICATE: 1 canonical (NEW) + N exact-content duplicates ---
    if only in (None, "dup") and n_dup > 0:
        dup_rule = next_type()
        dup_sev = next_sev()
        dup_iocs = gen_iocs(rng)                 # identical across the group
        c = cid()
        plan.append(("DUP-CANON", "NEW", build_event(
            client, c, f"{client.lower()}-dup-{run_tag}-canon", dup_rule, dup_sev, dup_iocs)))
        for i in range(n_dup):
            c = cid()
            plan.append(("DUPLICATE", "DUPLICATE", build_event(
                client, c, f"{client.lower()}-dup-{run_tag}-{i}",
                dup_rule, dup_sev, dict(dup_iocs))))   # same rule + same IOCs

    # --- SIMILAR: 1 canonical (NEW) + N fuzzy variants ---
    if only in (None, "similar") and n_similar > 0:
        sim_sev = next_sev()
        sim_base = next_type()
        sim_iocs = gen_iocs(rng)
        c = cid()
        plan.append(("SIM-CANON", "NEW", build_event(
            client, c, f"{client.lower()}-sim-{run_tag}-canon",
            f"{sim_base} {run_tag}", sim_sev, sim_iocs)))
        for i in range(1, n_similar + 1):
            variant = dict(sim_iocs)                       # keep 5 slots identical
            variant["requestURL"] = f"{sim_iocs['requestURL']}-{i}"  # vary ONE slot
            c = cid()
            plan.append(("SIMILAR", "SIMILAR", build_event(
                client, c, f"{client.lower()}-sim-{run_tag}-{i}",
                f"{sim_base} Variant {i}", sim_sev, variant)))  # DIFFERENT rule name each

    return plan


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

def post(base: str, api_key: str, client: str, body: dict, timeout: float):
    data = json.dumps(body).encode()
    req = urllib.request.Request(base + ENDPOINT, data=data, method="POST")
    req.add_header("content-type", "application/json")
    req.add_header("x-api-key", api_key)
    req.add_header("x-client-name", client)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode(), (time.time() - t0) * 1000
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), (time.time() - t0) * 1000
    except Exception as e:  # noqa: BLE001
        return 0, f"<transport error: {e}>", (time.time() - t0) * 1000


def verdict_of(status: int, text: str) -> str:
    try:
        return json.loads(text).get("verdict") or f"HTTP {status}"
    except json.JSONDecodeError:
        return f"HTTP {status}"


def load_env(path: str) -> dict:
    env = {}
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return env


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="Apollo base URL (or APOLLO_URL in .env)")
    ap.add_argument("--api-key", help="x-api-key (or APOLLO_API_KEY in .env)")
    ap.add_argument("--clients", default=",".join(DEFAULT_CLIENTS),
                    help="comma-separated tenant names (default: %(default)s)")
    ap.add_argument("--new", type=int, default=10,
                    help="NEW alerts per client (default 10 = one per alert type)")
    ap.add_argument("--dup", type=int, default=4, help="DUPLICATEs per client (default 4)")
    ap.add_argument("--similar", type=int, default=4, help="SIMILAR variants per client (default 4)")
    ap.add_argument("--only", choices=["new", "dup", "similar"],
                    help="generate only this group (default: all three)")
    ap.add_argument("--seed", type=int, help="RNG seed for reproducible IOCs")
    ap.add_argument("--sleep", type=float, default=0.1, help="seconds between requests")
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--dry-run", action="store_true", help="build + print payloads, send nothing")
    ap.add_argument("--yes", action="store_true", help="skip the prod confirmation countdown")
    args = ap.parse_args()

    env = load_env(".env")
    base = (args.url or env.get("APOLLO_URL", "")).rstrip("/")
    api_key = args.api_key or env.get("APOLLO_API_KEY", "")
    if not base or not api_key:
        print("ERROR: APOLLO_URL and APOLLO_API_KEY required (in .env or via flags).", file=sys.stderr)
        return 2

    clients = [c.strip() for c in args.clients.split(",") if c.strip()]
    run_tag = int(time.time())
    rng = random.Random(args.seed if args.seed is not None else run_tag)

    # Build the full plan first (deterministic, reviewable).
    plans = {}
    total = 0
    for idx, client in enumerate(clients):
        base_cid = 900_000_000 + (run_tag % 1_000_000) + idx * 100_000
        p = plan_client(client, base_cid, run_tag, rng,
                        args.new, args.dup, args.similar, args.only)
        plans[client] = p
        total += len(p)

    is_prod = "cosmos.tekstream.com" in base and "dev" not in base
    print("=" * 78)
    print(f"Target : {base}{ENDPOINT}   {'[PRODUCTION]' if is_prod else ''}")
    print(f"Clients: {', '.join(clients)}")
    print(f"Per client: new={args.new} dup={args.dup} similar={args.similar}"
          + (f"  (only={args.only})" if args.only else ""))
    print(f"Total synthetic alerts: {total}   Run tag: {run_tag}   Seed: {args.seed}")
    print(f"Auth   : x-api-key {api_key[:14]}... (masked)")
    print("=" * 78)

    if args.dry_run:
        for client, p in plans.items():
            print(f"\n########## {client} ({len(p)} alerts) ##########")
            for kind, expect, ev in p:
                cef = ev["artifacts"][0]["cef"]
                print(f"  [{kind:<9} expect={expect:<9}] {ev['container']['severity']:<8} "
                      f"{ev['container']['name'][:44]:<44} url={cef['requestURL']}")
        print("\nDRY RUN: nothing was sent.")
        return 0

    if is_prod and not args.yes:
        print("\n*** This injects REAL alerts into the PROD SOC. Ctrl-C to abort. Starting in 5s... ***")
        time.sleep(5)

    tallies: dict[str, dict[str, int]] = {}
    mismatches = 0
    for client, p in plans.items():
        print(f"\n########## {client} ##########")
        tally = tallies.setdefault(client, {})
        for i, (kind, expect, ev) in enumerate(p, 1):
            status, text, ms = post(base, api_key, client, ev, args.timeout)
            v = verdict_of(status, text)
            tally[v] = tally.get(v, 0) + 1
            flag = "" if (v == expect or kind.endswith("CANON")) else "  <-- MISMATCH"
            if flag:
                mismatches += 1
            print(f"  {i:>3}/{len(p)} [{kind:<9}] HTTP {status} {ms:5.0f}ms  "
                  f"verdict={v:<10} expect={expect}{flag}")
            time.sleep(args.sleep)

    print("\n" + "=" * 78)
    print("SUMMARY")
    grand: dict[str, int] = {}
    for client, tally in tallies.items():
        parts = "  ".join(f"{k}={v}" for k, v in sorted(tally.items()))
        print(f"  {client:<10} {parts}")
        for k, v in tally.items():
            grand[k] = grand.get(k, 0) + v
    print("  " + "-" * 40)
    print("  TOTAL      " + "  ".join(f"{k}={v}" for k, v in sorted(grand.items())))
    print(f"  verdict mismatches vs expected: {mismatches}")
    print("=" * 78)
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
