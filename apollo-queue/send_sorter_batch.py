#!/usr/bin/env python3
"""Send one SOAR-shaped batch of duplicate alerts to Apollo, out of container
id order, to exercise the container sorter (apollo#1369), and capture
everything needed to judge the run.

What it does:
  1. Preflight: GET /api/v1/ingest/soar/resolve?client=<client>. Aborts unless
     the client exists and is accepting ingestion. Nothing is sent otherwise.
  2. /health before the run (gitSha, uptime -> task boot time).
  3. Builds one alert per container id. All carry the SAME rule name and the
     SAME 6 IOCs (reserved/private values only), so dedup treats them as one
     cluster. Each CEF carries soar_instance (default LOCALDEVSOAR).
  4. Fires them the way SOAR does: one POST /api/v1/ingest/soar per container,
     all concurrent, started a few ms apart in the given order.
  5. /health after the run: a different boot time means the task restarted
     mid-test.
  6. Server-side record (only if a dashboard token is available, see below):
     reads Apollo's activity log for these alerts: SOAR push received and
     answered (with the gate's own duration), dedup request order, dedup
     verdicts, dedup.failed, rejections, and each alert's Ordering Queue
     (ingest.ordered) row.
  7. Checks, each PASS / WARN / FAIL:
       - every call HTTP 200
       - every alert filed as <client>-<container id>
       - SOAR round trip: WARN over 8 s, FAIL over 10 s (the gate budget)
       - exactly one NEW, all others DUPLICATE of it
       - the origin is the LOWEST container id (the sorter's job when on;
         when off it reports the arrival-order origin instead)
       - (server) every held alert has an Ordering Queue row
       - (server) dedup ran in ascending container order
       - (server) no dedup.failed, no rejection
       - no task restart during the run
  8. Writes captures/<ts>-<client>-sorter-batch.log (exactly what was printed)
     and .json (every request, response, header, timing, server row).

Credentials (never printed):
  - APOLLO_API_KEY in --env: the ingest key used for the SOAR gate.
  - APOLLO_DASHBOARD_TOKEN in --env (optional): a Cosmos SSO bearer token for
    a user with the soc_analyst role. The ingest key cannot read the activity
    log (403 "Requires one of: soc_analyst"). Without the token the server-side
    section is skipped and says so. Tokens expire; refresh when you get 401.

Usage:
  python3 send_sorter_batch.py --dry-run     # preflight + build, send nothing
  python3 send_sorter_batch.py               # send to prod (5 s countdown)
  python3 send_sorter_batch.py --yes --client tekstream --ids 999305,999301
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ENV = os.path.join(os.path.dirname(HERE), ".env")
DEFAULT_URL = "https://apollo.cosmos.tekstream.com"
INGEST = "/api/v1/ingest/soar"
RESOLVE = "/api/v1/ingest/soar/resolve"
HEALTH = "/health"
ACTIVITY = "/api/v1/dashboard/activity-log"
DEFAULT_IDS = "999005,999002,999004,999003,999001"
RULE = "Palo Alto - Critical Threat Allowed Inbound (sorter test)"
WARN_MS = 8_000
FAIL_MS = 10_000


# ---------------------------------------------------------------- output ---

class Tee:
    """Everything printed also goes to the run's .log file."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, *parts: object) -> None:
        line = " ".join(str(p) for p in parts)
        print(line)
        self.lines.append(line)


# ------------------------------------------------------------------ config ---

def load_env(path: str) -> dict:
    env: dict[str, str] = {}
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return env


# --------------------------------------------------------------- transport ---

def http(method: str, url: str, headers: dict, body: dict | None, timeout: float) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    if data is not None:
        req.add_header("content-type", "application/json")
    sent_at = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    resp_headers: dict[str, str] = {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, text = resp.status, resp.read().decode()
            resp_headers = dict(resp.headers.items())
    except urllib.error.HTTPError as e:
        status, text = e.code, e.read().decode()
        resp_headers = dict(e.headers.items()) if e.headers else {}
    except Exception as e:  # noqa: BLE001
        status, text = 0, f"<transport error: {e}>"
    ms = (time.perf_counter() - t0) * 1000
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = text
    return {"sent_at": sent_at, "status": status, "ms": ms, "body": parsed,
            "headers": {k.lower(): v for k, v in resp_headers.items()
                        if k.lower() in ("x-correlation-id", "x-request-id", "date", "content-type")}}


def health(base: str, timeout: float) -> dict:
    r = http("GET", base + HEALTH, {}, None, timeout)
    b = r["body"] if isinstance(r["body"], dict) else {}
    boot = None
    if "timestamp" in b and "uptime" in b:
        ts = datetime.fromisoformat(b["timestamp"].replace("Z", "+00:00")).timestamp()
        boot = datetime.fromtimestamp(ts - float(b["uptime"]), timezone.utc).isoformat(timespec="seconds")
    return {"status": r["status"], "health": b.get("status"), "gitSha": (b.get("gitSha") or "")[:8], "task_boot": boot}


# ---------------------------------------------------------------- payloads ---

def build_iocs(rng: random.Random) -> dict:
    """One IOC set shared by the whole batch. Reserved/private values only,
    with a per-run token so no real alert (or earlier test) can match."""
    tok = "%07x" % rng.getrandbits(28)
    return {
        "sourceAddress": f"10.{rng.randint(1, 254)}.{rng.randint(0, 254)}.{rng.randint(1, 254)}",
        "destinationAddress": f"172.{rng.randint(16, 31)}.{rng.randint(0, 254)}.{rng.randint(1, 254)}",
        "sourceUserName": f"sortertest_{tok}",
        "destinationHostName": f"host-{rng.randint(1, 9999)}.corp{tok}.example",
        "fileHashSha256": "%064x" % rng.getrandbits(256),
        "requestURL": f"http://c2-{rng.randint(100, 999)}.badnet{tok}.example/{rng.randint(10000, 99999)}/payload",
    }


def build_event(client: str, container_id: int, soar_instance: str, iocs: dict) -> dict:
    # Shaped like a real SOAR notable CEF (rule/source/search fields alongside
    # the IOC slots), identical across the batch so dedup sees one cluster.
    cef = {
        "ClientName": client,
        "soar_instance": soar_instance,
        "rule_name": RULE,
        "source": f"Threat - {RULE} - Rule",
        "search_name": f"Threat - {RULE} - Rule",
        "severity": "high",
        "urgency": "high",
        "security_domain": "network",
        "label": "generic",
    }
    cef.update(iocs)
    return {
        "container": {
            "id": container_id,
            "name": RULE,
            "tenant_name": client,
            "severity": "high",
            "source_data_identifier": f"{client.lower()}-sortertest-{container_id}",
        },
        "artifacts": [{"label": "artifact", "name": "network-observable", "cef": cef}],
    }


# ------------------------------------------------------------- server side ---

def server_record(base: str, token: str, alert_ids: list[str], customer_id: str,
                  since_iso: str, timeout: float) -> dict:
    """Apollo's own audit rows for this run. Needs a soc_analyst bearer token."""
    hdr = {"authorization": f"Bearer {token}"}
    out: dict = {"per_alert": {}, "rejected": [], "errors": []}
    per_alert_types = ["ingest.received", "ingest.ordered", "ingest.responded", "dedup.request", "dedup.verdict",
                       "dedup.failed"]
    for aid in alert_ids:
        rows = {}
        for t in per_alert_types:
            q = urllib.parse.urlencode({"event_type": t, "alert_id": aid, "alert_id_match": "exact", "limit": 5})
            r = http("GET", f"{base}{ACTIVITY}?{q}", hdr, None, timeout)
            if r["status"] != 200:
                out["errors"].append(f"{t} {aid}: HTTP {r['status']}")
                continue
            data = (r["body"] or {}).get("data") or []
            rows[t] = data[0] if data else None
        out["per_alert"][aid] = rows
    for t, key in (("ingest.rejected", "rejected"),):
        q = urllib.parse.urlencode({"event_type": t, "customer_id": customer_id, "date_from": since_iso,
                                    "limit": 20, "sort": "created_at", "order": "asc"})
        r = http("GET", f"{base}{ACTIVITY}?{q}", hdr, None, timeout)
        if r["status"] == 200:
            out[key] = (r["body"] or {}).get("data") or []
        else:
            out["errors"].append(f"{t}: HTTP {r['status']}")
    return out


# -------------------------------------------------------------------- main ---

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--env", default=DEFAULT_ENV, help="file holding APOLLO_API_KEY (and optional APOLLO_DASHBOARD_TOKEN)")
    ap.add_argument("--client", default="tekstream-admin")
    ap.add_argument("--soar-instance", default="LOCALDEVSOAR")
    ap.add_argument("--ids", default=DEFAULT_IDS, help="container ids, in SEND order")
    ap.add_argument("--stagger-ms", type=float, default=5.0, help="gap between request starts")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--settle-s", type=float, default=10.0, help="wait before reading the server record (audit flush)")
    ap.add_argument("--dry-run", action="store_true", help="preflight + print the batch, send nothing")
    ap.add_argument("--yes", action="store_true", help="skip the countdown")
    args = ap.parse_args()

    log = Tee()
    env = load_env(args.env)
    key = env.get("APOLLO_API_KEY")
    if not key:
        sys.exit(f"ERROR: APOLLO_API_KEY not found in {args.env}")
    dash_token = env.get("APOLLO_DASHBOARD_TOKEN")
    base = args.url.rstrip("/")
    ids = [int(x) for x in args.ids.split(",") if x.strip()]
    rng = random.Random(int(time.time()))
    iocs = build_iocs(rng)
    events = [build_event(args.client, cid, args.soar_instance, iocs) for cid in ids]
    ingest_hdr = {"x-api-key": key, "x-client-name": args.client}
    run_started = datetime.now(timezone.utc)

    log("=" * 78)
    log(f"Run      : {run_started.isoformat(timespec='seconds')}")
    log(f"Target   : {base}{INGEST}")
    log(f"Client   : {args.client}   soar_instance: {args.soar_instance}")
    log(f"Send order: {', '.join(str(i) for i in ids)}  (concurrent, {args.stagger_ms:g} ms apart)")
    log(f"Shared IOCs: {json.dumps(iocs)}")
    log(f"Auth     : ingest key from {args.env}; dashboard token {'present' if dash_token else 'ABSENT (server-side section skipped)'}")
    log("=" * 78)

    # 1. Preflight.
    q = urllib.parse.urlencode({"client": args.client})
    pre = http("GET", f"{base}{RESOLVE}?{q}", ingest_hdr, None, args.timeout)
    pb = pre["body"] if isinstance(pre["body"], dict) else {}
    log(f"Preflight: HTTP {pre['status']} customer={pb.get('customerId')} slug={pb.get('customerSlug')} "
        f"status={pb.get('status')} accepting={pb.get('acceptingIngestion')}")
    if pre["status"] != 200 or pb.get("acceptingIngestion") is not True:
        log("ABORT: client does not resolve or is not accepting ingestion. Nothing was sent.")
        return 2
    slug = pb.get("customerSlug") or args.client
    customer_id = pb.get("customerId") or ""

    if args.dry_run:
        log(json.dumps(events[0], indent=2))
        log(f"\nDRY RUN: {len(events)} events built, nothing sent.")
        return 0

    # 2. Health before.
    h_before = health(base, args.timeout)
    log(f"Health before: {h_before}")

    if not args.yes:
        log("\n*** Sends REAL alerts to PRODUCTION. Ctrl-C to abort. Starting in 5 s... ***")
        time.sleep(5)

    # 3. Fire concurrently, started in the given order a few ms apart.
    results: dict[int, dict] = {}
    t_start = time.perf_counter()

    def send(ev: dict) -> None:
        cid = ev["container"]["id"]
        started = (time.perf_counter() - t_start) * 1000
        r = http("POST", base + INGEST, ingest_hdr, ev, args.timeout)
        r["start_ms"] = started
        results[cid] = r

    threads = []
    for ev in events:
        th = threading.Thread(target=send, args=(ev,))
        th.start()
        threads.append(th)
        time.sleep(args.stagger_ms / 1000)
    for th in threads:
        th.join()

    # 4. Client-side report.
    log(f"\n{'container':>10} {'start':>8} {'HTTP':>5} {'rtt':>7}  {'verdict':<10} {'apollo_alert_id':<26} {'canonical':<26} correlation")
    for cid in ids:
        r = results.get(cid, {})
        b = r.get("body") if isinstance(r.get("body"), dict) else {}
        log(f"{cid:>10} {r.get('start_ms', 0):7.1f}ms {r.get('status', 0):>5} {r.get('ms', 0):6.0f}ms  "
            f"{str(b.get('verdict', '-')):<10} {str(b.get('apollo_alert_id') or '-'):<26} "
            f"{str(b.get('canonical_alert_id') or '-'):<26} {b.get('correlation_id') or r.get('headers', {}).get('x-correlation-id', '-')}")

    # 5. Health after.
    h_after = health(base, args.timeout)
    log(f"\nHealth after : {h_after}")

    # 6. Server-side record.
    alert_ids = [f"{slug}-{cid}" for cid in ids]
    server = None
    if dash_token:
        log(f"\nWaiting {args.settle_s:g} s for the audit log to flush, then reading the server record...")
        time.sleep(args.settle_s)
        server = server_record(base, dash_token, alert_ids, customer_id,
                               run_started.isoformat(timespec="seconds").replace("+00:00", "Z"), args.timeout)
        if server["errors"]:
            log("Server record errors: " + "; ".join(server["errors"][:5]))
        log(f"\n{'alert':<26} {'received':>12} {'dedup req':>12} {'verdict':>12} {'gate ms':>8}  verdict -> canonical")
        for aid in alert_ids:
            rows = server["per_alert"].get(aid, {})

            def at(t: str) -> str:
                row = rows.get(t)
                return row["created_at"][11:23] if row else "-"

            v = rows.get("dedup.verdict") or {}
            resp = rows.get("ingest.responded") or {}
            vd = v.get("details") or {}
            log(f"{aid:<26} {at('ingest.received'):>12} {at('dedup.request'):>12} {at('dedup.verdict'):>12} "
                f"{str(resp.get('duration_ms', '-')):>8}  {vd.get('verdict', '-')} -> {vd.get('canonical_alert_id') or '-'}")
        for aid in alert_ids:
            o = server["per_alert"][aid].get("ingest.ordered")
            if o:
                d = o.get("details") or {}
                log(f"Ordering Queue {aid}: arrival {d.get('arrival_position')} -> release {d.get('release_position')}"
                    f" of {d.get('queue_size')}, waited {d.get('waited_ms')} ms, reason {d.get('release_reason')}"
                    f", early {d.get('released_early')}")
        for rj in server["rejected"]:
            log(f"REJECTED {rj.get('created_at')}: {json.dumps(rj.get('details', {}).get('code'))}")
    else:
        log("\nServer record: SKIPPED (no APOLLO_DASHBOARD_TOKEN in the env file).")

    # 7. Checks.
    checks: list[tuple[str, str, str]] = []

    def check(name: str, ok: bool, detail: str, warn: bool = False) -> None:
        checks.append(("PASS" if ok else ("WARN" if warn else "FAIL"), name, detail))

    statuses = {cid: results.get(cid, {}).get("status") for cid in ids}
    check("HTTP 200 on every call", all(s == 200 for s in statuses.values()), json.dumps(statuses))
    bodies = {cid: (results.get(cid, {}).get("body") if isinstance(results.get(cid, {}).get("body"), dict) else {}) for cid in ids}
    wrong_ids = [cid for cid in ids if bodies[cid].get("apollo_alert_id") not in (None, f"{slug}-{cid}")]
    check("alerts filed under the client", not wrong_ids, f"wrong: {wrong_ids}" if wrong_ids else f"all {slug}-<id>")
    rtts = [results.get(cid, {}).get("ms", 0) for cid in ids]
    worst = max(rtts) if rtts else 0
    if worst > FAIL_MS:
        check("SOAR round trip within budget", False, f"worst {worst:.0f} ms > {FAIL_MS} ms")
    else:
        check("SOAR round trip within budget", worst <= WARN_MS, f"worst {worst:.0f} ms (warn > {WARN_MS})", warn=True)
    verdicts = {cid: bodies[cid].get("verdict") for cid in ids}
    news = [cid for cid, v in verdicts.items() if v == "NEW"]
    dups = [cid for cid, v in verdicts.items() if v == "DUPLICATE"]
    check("one NEW, the rest DUPLICATE", len(news) == 1 and len(dups) == len(ids) - 1, json.dumps(verdicts))
    held = min(rtts) > 3_000 if rtts else False  # a held batch takes at least the quiet period
    if news:
        origin = news[0]
        lowest = min(ids)
        if held:
            check("origin is the lowest container (sorter on)", origin == lowest, f"origin {origin}, lowest {lowest}")
        else:
            checks.append(("INFO", "origin by arrival (not held: queue off)", f"origin {origin}, lowest {lowest}"))
    if server and not server["errors"]:
        order = sorted(alert_ids, key=lambda a: (server["per_alert"][a].get("dedup.request") or {}).get("created_at", "~"))
        expected = [f"{slug}-{c}" for c in sorted(ids)]
        if held:
            check("(server) dedup ran in ascending container order", order == expected, " ".join(a.rsplit("-", 1)[1] for a in order))
        failed = [a for a in alert_ids if server["per_alert"][a].get("dedup.failed")]
        check("(server) no dedup.failed", not failed, f"failed: {failed}" if failed else "none")
        check("(server) no rejected pushes", not server["rejected"], f"{len(server['rejected'])} rejected")
        if held:
            missing = [a for a in alert_ids if not server["per_alert"][a].get("ingest.ordered")]
            check("(server) every held alert has an Ordering Queue row", not missing,
                  f"missing: {missing}" if missing else f"{len(alert_ids)} row(s)")
    restarted = h_before.get("task_boot") != h_after.get("task_boot")
    check("no task restart during the run", not restarted, f"boot {h_before.get('task_boot')} -> {h_after.get('task_boot')}")

    log("\nCHECKS")
    for status, name, detail in checks:
        log(f"  [{status}] {name}: {detail}")
    overall = "FAIL" if any(s == "FAIL" for s, _, _ in checks) else ("WARN" if any(s == "WARN" for s, _, _ in checks) else "PASS")
    log(f"  OVERALL: {overall}")

    # 8. Save.
    os.makedirs(os.path.join(HERE, "captures"), exist_ok=True)
    stem = os.path.join(HERE, "captures", time.strftime("%Y%m%d-%H%M%S") + f"-{args.client}-sorter-batch")
    with open(stem + ".json", "w") as fh:
        json.dump({"run_started": run_started.isoformat(), "target": base + INGEST, "client": args.client,
                   "customer_id": customer_id, "send_order": ids, "iocs": iocs, "events": events,
                   "preflight": pre, "health_before": h_before, "health_after": h_after,
                   "results": results, "server": server,
                   "checks": [{"status": s, "check": n, "detail": d} for s, n, d in checks],
                   "overall": overall}, fh, indent=2)
    log(f"\nSaved: {stem}.log")
    log(f"Saved: {stem}.json")
    with open(stem + ".log", "w") as fh:
        fh.write("\n".join(log.lines) + "\n")
    return 1 if overall == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
