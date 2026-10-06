"""
Alert Streaming Engine — Reads STIX bundles from JSON files and streams them
one-by-one to the dedup API endpoint POST /api/v1/alerts.

Usage:
    python stream_alerts.py
"""

import glob
import json
import os
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


BASE_URL = "http://localhost:8042"
API_URL = f"{BASE_URL}/api/v1/alerts"
FLUSH_URL = f"{BASE_URL}/api/v1/admin/flush"
BUNDLES_DIR = os.path.join(os.path.dirname(__file__), "stix_bundles")


def _flush_redis(label: str):
    print(f"[FLUSH] {label}...")
    try:
        req = Request(FLUSH_URL, method="DELETE")
        with urlopen(req, timeout=10) as resp:
            resp.read()
        print(f"[FLUSH] {label} — done.")
    except Exception as e:
        print(f"[FLUSH] {label} — failed: {e}")


def stream_alerts() -> None:
    _flush_redis("Pre-stream flush")

    bundle_files = sorted(glob.glob(os.path.join(BUNDLES_DIR, "*.json")))
    if not bundle_files:
        print(f"No JSON files found in {BUNDLES_DIR}")
        sys.exit(1)

    alerts = []
    for fpath in bundle_files:
        with open(fpath, encoding="utf-8") as f:
            alerts.append(json.load(f))

    alerts.sort(key=lambda a: a.get("created_at", ""))

    total = len(alerts)
    counters: dict[str, int] = {}
    layer_counts: dict[int, int] = {}
    errors = 0
    start_time = time.time()

    print(f"Streaming {total} alerts to {API_URL}")
    print("-" * 70)

    for i, alert in enumerate(alerts, 1):
        alert_id = alert.get("alert_id", "")
        severity = alert.get("severity", "")

        payload = {
            "stix_bundle": alert["stix_bundle"],
            "source": alert.get("source", ""),
            "severity": severity,
            "customer_id": alert.get("customer_id", ""),
            "alert_id": alert_id,
            "created_at": alert.get("created_at", "").strip('"'),
        }

        try:
            body = json.dumps(payload).encode("utf-8")
            req = Request(
                API_URL,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())

            verdict = result["verdict"]
            layer = result["layer"]
            hash_conf = result["hash_confidence"]
            ctx_sim = result["stix_context_similarity"]
            obs_type = result["stix_observable_by_type"]
            obs_val = result["stix_observable_by_value"]
            matched = result.get("matched_alert_id", "")

            counters[verdict] = counters.get(verdict, 0) + 1
            layer_counts[layer] = layer_counts.get(layer, 0) + 1

            icon = {"DUPLICATE": "x", "SIMILAR": "~", "NEW": "."}[verdict]
            match_info = f" -> {matched[:12]}" if matched else ""
            scores = f"hash={hash_conf:.2f}"
            if ctx_sim > 0:
                scores += f" obj={ctx_sim:.0f}"
            if obs_type > 0:
                scores += f" obs_t={obs_type:.0f}"
            if obs_val > 0:
                scores += f" obs_v={obs_val:.0f}"
            print(
                f"[{i:>4}/{total}] {icon} {verdict:<10} "
                f"L{layer} {scores}  "
                f"{severity:<6} {alert_id[:16]}{match_info}"
            )

        except HTTPError as e:
            errors += 1
            err_body = e.read().decode() if e.fp else ""
            print(f"[{i:>4}/{total}] ! HTTP {e.code}: {err_body[:100]}")
        except URLError as e:
            errors += 1
            print(f"[{i:>4}/{total}] ! Connection error: {e.reason}")
            if i == 1:
                print(f"\nIs the server running?")
                sys.exit(1)

    elapsed = time.time() - start_time
    rate = total / elapsed if elapsed > 0 else 0

    print()
    print("=" * 70)
    print("STREAM COMPLETE")
    print("=" * 70)
    print(f"  Total alerts:    {total}")
    print(f"  Errors:          {errors}")
    print(f"  Elapsed:         {elapsed:.1f}s ({rate:.1f} alerts/sec)")
    print()
    print("  Verdicts:")
    print(f"    DUPLICATE:     {counters.get('DUPLICATE', 0):>4}  ({_pct(counters.get('DUPLICATE', 0), total)})")
    print(f"    SIMILAR:       {counters.get('SIMILAR', 0):>4}  ({_pct(counters.get('SIMILAR', 0), total)})")
    print(f"    NEW:           {counters.get('NEW', 0):>4}  ({_pct(counters.get('NEW', 0), total)})")
    print()
    print("  Layer breakdown:")
    print(f"    Layer 1 (hash):       {layer_counts.get(1, 0):>4}")
    print(f"    Layer 2 (fuzzy):      {layer_counts.get(2, 0):>4}")
    print(f"    No match:             {layer_counts.get(0, 0):>4}")
    print("=" * 70)

    print()
    _flush_redis("Post-stream flush")


def _pct(count: int, total: int) -> str:
    if total == 0:
        return "0.0%"
    return f"{count / total * 100:.1f}%"


if __name__ == "__main__":
    stream_alerts()
