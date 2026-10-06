#!/usr/bin/env python3
"""Push a real SOAR notable JSON file to the Apollo DEV dedup/ingest gate.

Unlike soar_gate_test.py (which builds a synthetic event), this posts an exact
on-disk payload verbatim — for pushing a real SOAR container as-is.

Config from dev/.env (APOLLO_URL, APOLLO_API_KEY, APOLLO_CUSTOMER_ID) or flags.

  ./push_first_alert.py                       # pushes ../../mitreattack-tagging/docs/apollo-post-body.json
  ./push_first_alert.py --file <path.json>    # push a specific file
  ./push_first_alert.py --no-readback         # skip GET /alerts/:id
"""
import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, ".env")
DEFAULT_PAYLOAD = os.path.abspath(
    os.path.join(HERE, "..", "..", "..", "mitreattack-tagging", "docs", "apollo-post-body.json")
)


def load_env(path):
    env = {}
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def http(method, url, api_key, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("x-api-key", api_key)
    if data is not None:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"_raw": raw}


def main():
    ap = argparse.ArgumentParser(description="Push a real SOAR notable file to Apollo DEV")
    ap.add_argument("--file", default=DEFAULT_PAYLOAD, help="path to the SOAR post-body JSON")
    ap.add_argument("--url", help="Apollo base URL (or APOLLO_URL in dev/.env)")
    ap.add_argument("--api-key", help="x-api-key (or APOLLO_API_KEY in dev/.env)")
    ap.add_argument("--no-readback", action="store_true", help="skip GET /alerts/:id")
    args = ap.parse_args()

    env = load_env(ENV_PATH)
    url = (args.url or env.get("APOLLO_URL") or "").rstrip("/")
    api_key = args.api_key or env.get("APOLLO_API_KEY") or ""
    if not url or not api_key:
        sys.exit(f"ERROR: need APOLLO_URL + APOLLO_API_KEY (dev/.env or flags).")

    if not os.path.exists(args.file):
        sys.exit(f"ERROR: payload file not found: {args.file}")
    with open(args.file) as f:
        payload = json.load(f)

    c = payload.get("container", {})
    print(f"POST {url}/api/v1/ingest/soar")
    print(f"  file            : {args.file}")
    print(f"  container.id    : {c.get('id')}")
    print(f"  container.name  : {c.get('name')}")
    print(f"  sdi             : {c.get('source_data_identifier')}")
    print(f"  ClientName      : {(payload.get('artifacts') or [{}])[0].get('cef', {}).get('ClientName')}")
    print(f"  artifacts       : {len(payload.get('artifacts') or [])}")

    t0 = time.time()
    status, body = http("POST", f"{url}/api/v1/ingest/soar", api_key, payload)
    ms = int((time.time() - t0) * 1000)
    print(f"\n[push]  HTTP {status}  {ms}ms")
    if status != 200:
        print(json.dumps(body, indent=2)[:1200])
        sys.exit(1 if status >= 400 else 0)

    for k in ("transition_state", "verdict", "is_duplicate", "confidence",
              "match_type", "apollo_alert_id", "canonical_alert_id", "correlation_id", "reasoning"):
        if k in body:
            print(f"  {k:17}: {body[k]}")

    alert_id = body.get("apollo_alert_id") or body.get("canonical_alert_id")
    if not args.no_readback and alert_id:
        st, ab = http("GET", f"{url}/api/v1/alerts/{alert_id}", api_key)
        d = ab.get("data", ab) if isinstance(ab, dict) else {}
        if st == 200:
            print(f"\n  read-back        : [STORED] id={alert_id}")
            print(f"      status={d.get('status')}  customer_id={d.get('customer_id') or d.get('customerId')}  "
                  f"stix_bundle={'present' if d.get('stix_bundle') or d.get('stixBundle') else 'MISSING'}  "
                  f"dedup_count={d.get('dedup_count', d.get('dedupCount'))}")
        else:
            print(f"\n  read-back        : GET /alerts/{alert_id} -> HTTP {st}")


if __name__ == "__main__":
    main()
