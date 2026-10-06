#!/usr/bin/env python3
"""
Dedup verdict endpoint test (apollo#1044, shipped in apollo#1045).

    GET /api/v1/dedup/verdict/{containerId}   header: x-api-key

Reads APOLLO_API_KEY from ../.env (the poc/apollo folder) unless the variable
is already set in the environment. Targets prod by default.

Usage:
    # One container
    python3 verdict.py 990008

    # Several containers on dev
    python3 verdict.py --env dev 990008 990009

    # Contract checks that need no known container (400 / 401 / 404)
    python3 verdict.py --smoke

    # Explicit base URL
    python3 verdict.py --url https://apollo-dev.cosmos.tekstream.com 990008

Every run appends a JSON line per request to logs/verdict_<UTC date>.log.
Exit code is 1 when any response fails the contract check.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE.parent / ".env"

ENVS = {
    "dev": "https://apollo-dev.cosmos.tekstream.com",
    "prod": "https://apollo.cosmos.tekstream.com",
}

ROLES = {"parent", "child"}
VERDICTS = {"NEW", "DUPLICATE", "SIMILAR"}
RECOMMENDATIONS = {"auto_close", "review_required", None}
ERROR_CODES = {
    "container_id_invalid",
    "key_not_tenant_bound",
    "container_not_found",
    "container_ambiguous",
    "origin_unresolved",
}


def load_api_key() -> str:
    key = os.environ.get("APOLLO_API_KEY")
    if key:
        return key
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("APOLLO_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit(f"APOLLO_API_KEY not set and not found in {ENV_FILE}")


def call(base: str, container_id: str, api_key: str | None, timeout: float = 20.0):
    url = f"{base.rstrip('/')}/api/v1/dedup/verdict/{container_id}"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key
    req = request.Request(url, headers=headers, method="GET")
    started = time.perf_counter()
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            status, raw = resp.status, resp.read()
    except error.HTTPError as e:
        status, raw = e.code, e.read()
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    try:
        body = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        body = raw.decode("utf-8", "replace")
    return url, status, body, elapsed_ms


def check_success(body) -> list[str]:
    """Contract for a 200 body (src/routes/dedup-verdict.ts dedupVerdictResponseSchema)."""
    problems = []
    if not isinstance(body, dict):
        return ["body is not an object"]
    for field in ("container_id", "role", "verdict", "parent", "child", "fingerprints_match", "recommendation", "reason"):
        if field not in body:
            problems.append(f"missing field {field}")
    if body.get("role") not in ROLES:
        problems.append(f"role {body.get('role')!r} not in {sorted(ROLES)}")
    if body.get("verdict") not in VERDICTS:
        problems.append(f"verdict {body.get('verdict')!r} not in {sorted(VERDICTS)}")
    if body.get("recommendation") not in RECOMMENDATIONS:
        problems.append(f"recommendation {body.get('recommendation')!r} unexpected")

    def check_side(name, side, extra=()):
        if side is None:
            return
        if not isinstance(side, dict):
            problems.append(f"{name} is not an object")
            return
        if not isinstance(side.get("container_id"), str):
            problems.append(f"{name}.container_id not a string")
        fp = side.get("entity_fingerprint")
        if fp is not None and not (isinstance(fp, str) and len(fp) == 7):
            problems.append(f"{name}.entity_fingerprint {fp!r} is not a 7-char short hash")
        for f in extra:
            if f not in side:
                problems.append(f"{name}.{f} missing")

    check_side("parent", body.get("parent"))
    check_side("child", body.get("child"))
    check_side("self", body.get("self"), extra=("child_count",))

    if body.get("role") == "parent":
        if body.get("verdict") != "NEW":
            problems.append("parent must report verdict NEW")
        if body.get("parent") is not None or body.get("child") is not None:
            problems.append("parent must have parent=null and child=null")
        if body.get("self") is None:
            problems.append("parent must carry self")
        if body.get("recommendation") is not None or body.get("fingerprints_match") is not None:
            problems.append("parent must have recommendation=null and fingerprints_match=null")
    elif body.get("role") == "child":
        if body.get("verdict") not in {"DUPLICATE", "SIMILAR"}:
            problems.append("child must report DUPLICATE or SIMILAR")
        if body.get("parent") is None or body.get("child") is None:
            problems.append("child must carry parent and child")
        match = body.get("fingerprints_match")
        if not isinstance(match, bool):
            problems.append("child fingerprints_match must be a boolean")
        else:
            expected = "auto_close" if match else "review_required"
            if body.get("recommendation") != expected:
                problems.append(f"recommendation {body.get('recommendation')!r}, expected {expected!r} for fingerprints_match={match}")
            p = (body.get("parent") or {}).get("entity_fingerprint")
            c = (body.get("child") or {}).get("entity_fingerprint")
            if match and (p is None or c is None or p != c):
                problems.append("fingerprints_match=true but short fingerprints missing or differ")
            if not match and p is not None and c is not None and p == c:
                # Short forms can collide while the full digests differ; note, do not fail.
                print("  note: short fingerprints equal but fingerprints_match=false (full digests differ)")
    return problems


def check_error(status: int, body, container_id: str) -> list[str]:
    problems = []
    if not isinstance(body, dict):
        return ["error body is not an object"]
    if status == 401:
        for f in ("error", "message"):
            if f not in body:
                problems.append(f"401 body missing {f}")
        return problems
    for f in ("error", "statusCode", "message", "code", "container_id", "correlationId"):
        if f not in body:
            problems.append(f"error body missing {f}")
    if body.get("statusCode") != status:
        problems.append(f"statusCode {body.get('statusCode')} != HTTP {status}")
    if body.get("code") not in ERROR_CODES:
        problems.append(f"code {body.get('code')!r} unexpected")
    if body.get("container_id") != container_id:
        problems.append(f"container_id echo {body.get('container_id')!r} != {container_id!r}")
    return problems


def log_line(log_path: Path, record: dict):
    with log_path.open("a") as fh:
        fh.write(json.dumps(record) + "\n")


def run_one(base, container_id, api_key, expect, log_path) -> bool:
    url, status, body, elapsed_ms = call(base, container_id, api_key)
    print(f"\nGET {url}")
    print(f"  HTTP {status}  {elapsed_ms} ms")
    print("  " + json.dumps(body, indent=2).replace("\n", "\n  "))
    problems = []
    if expect is not None and status != expect:
        problems.append(f"expected HTTP {expect}, got {status}")
    if status == 200:
        problems += check_success(body)
    else:
        problems += check_error(status, body, container_id)
    ok = not problems
    for p in problems:
        print(f"  FAIL: {p}")
    if ok:
        print("  OK: contract check passed")
    log_line(log_path, {
        "at": datetime.now(timezone.utc).isoformat(),
        "url": url, "status": status, "elapsed_ms": elapsed_ms,
        "expected": expect, "ok": ok, "problems": problems, "body": body,
    })
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("containers", nargs="*", help="SOAR container ids (numeric)")
    ap.add_argument("--env", choices=ENVS, default="prod")
    ap.add_argument("--url", help="base URL, overrides --env")
    ap.add_argument("--expect", type=int, help="expected HTTP status for every container given")
    ap.add_argument("--smoke", action="store_true", help="run the 400 / 401 / 404 contract checks")
    args = ap.parse_args()

    base = args.url or ENVS[args.env]
    api_key = load_api_key()
    log_path = HERE / "logs" / f"verdict_{datetime.now(timezone.utc):%Y-%m-%d}.log"
    print(f"target: {base}")

    results = []
    if args.smoke:
        print("\n== smoke: contract checks that need no known container ==")
        results.append(run_one(base, "not-a-number", api_key, 400, log_path))
        results.append(run_one(base, "1", None, 401, log_path))
        results.append(run_one(base, "999999999999", api_key, 404, log_path))
    for cid in args.containers:
        results.append(run_one(base, cid, api_key, args.expect, log_path))

    if not results:
        ap.print_help()
        return 2
    passed = sum(results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
