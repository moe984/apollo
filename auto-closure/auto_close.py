#!/usr/bin/env python3
"""
Auto-close an Apollo alert through the SOAR push gate's workflow-status endpoint.

    PATCH /api/v1/alerts/{alertId}/workflow-status

This is the upstream/automation half of the pair. The dashboard has an
SSO-gated twin at /api/v1/dashboard/alerts/{id}/workflow-status; both hand the
write to the same service, so a status set here shows on the alert page and the
Alert Queue exactly as an analyst's own change would.

The engine status of the alert is never touched by this path, only the
analyst/automation workflow status. Every move is reversible: an alert set to
auto_closed here can be set back to open, from either route.

Auth is the gate's x-api-key, the same credential that posts to
POST /api/v1/ingest/soar.

Logging. Two sinks, because they answer different questions:
  logs/auto-close.log  human-readable, rotating, every run appended
  logs/runs.jsonl      one JSON object per run, for "what did we close, and when"
Apollo itself keeps no audit row for this path (operator decision 2026-09-14:
the change is recorded on the alert and on the Cosmos bus, nothing goes to
audit_events), so this file IS the caller-side record of what automation did.
The API key is never written to either sink, only its sha256 prefix.

Usage:
    python3 auto_close.py                          # auto-close mccrary-7802 in prod
    python3 auto_close.py --dry-run                # show the request, send nothing
    python3 auto_close.py --alert-id lsu-245535 --env dev
    python3 auto_close.py --status open --reason "reopened for review"
    python3 auto_close.py --log-level DEBUG        # adds request/response detail
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
# The .env sits one level up, beside the other Apollo POC work.
ENV_PATH = HERE.parent / ".env"
API_KEY_VAR = "APOLLO_AUTO_CLOSURE_API_KEY"

LOG_DIR = HERE / "logs"
TEXT_LOG = LOG_DIR / "auto-close.log"
RUN_LOG = LOG_DIR / "runs.jsonl"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUPS = 5

BASE_URL = {
    "production": "https://apollo.cosmos.tekstream.com",
    "dev": "https://apollo-dev.cosmos.tekstream.com",
}

# src/db/schema/alerts.ts, ALERT_WORKFLOW_STATUSES. Any of these may be set
# from any other: there is no terminal status and no role gate.
WORKFLOW_STATUSES = (
    "open",
    "preparing",
    "under_review",
    "under_investigation",
    "escalated",
    "customer_responded",
    "closed",
    "auto_closed",
)

DEFAULT_ALERT_ID = "mccrary-7802"
DEFAULT_STATUS = "auto_closed"
DEFAULT_REASON = "Auto-closed by the Apollo auto-closure POC."
TIMEOUT_SECONDS = 30

log = logging.getLogger("auto-close")


def setup_logging(level: str, log_dir: Path) -> None:
    """Console at the chosen level, file always at DEBUG so the record is full."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.DEBUG)
    log.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, level))
    console.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(console)

    to_file = RotatingFileHandler(
        log_dir / TEXT_LOG.name, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS
    )
    to_file.setLevel(logging.DEBUG)
    to_file.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s [%(run_id)s] %(message)s")
    )
    log.addHandler(to_file)


def bind_run_id(run_id: str) -> None:
    """Stamp every record with the run id so interleaved runs stay separable."""
    logging.setLogRecordFactory(
        _stamping_factory(logging.getLogRecordFactory(), run_id)
    )


def _stamping_factory(inner, run_id: str):
    def factory(*args, **kwargs):
        record = inner(*args, **kwargs)
        record.run_id = run_id
        return record

    return factory


def write_run_record(log_dir: Path, record: dict) -> None:
    """Append one JSON object per run. Never holds the key, only its prefix."""
    try:
        with (log_dir / RUN_LOG.name).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError as err:
        # A run that cannot write its own record still succeeded or failed on
        # its own terms; say so loudly rather than masking the API result.
        log.error("Could not append to %s: %s", RUN_LOG.name, err)


def load_api_key() -> str:
    """Read the gate key. The value is never printed or logged."""
    if not ENV_PATH.is_file():
        log.error("No .env at %s", ENV_PATH)
        sys.exit(1)
    load_dotenv(ENV_PATH)
    key = os.environ.get(API_KEY_VAR, "").strip()
    if not key:
        log.error("%s is not set in %s", API_KEY_VAR, ENV_PATH)
        sys.exit(1)
    log.debug("Loaded %s from %s", API_KEY_VAR, ENV_PATH)
    return key


def fingerprint(key: str) -> str:
    """A stable handle for 'which key was used' that leaks nothing."""
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def set_workflow_status(
    base_url: str, api_key: str, alert_id: str, status: str, reason: str | None
) -> requests.Response:
    body: dict[str, str] = {"status": status}
    if reason:
        body["reason"] = reason
    log.debug("Request body: %s", json.dumps(body))
    return requests.patch(
        f"{base_url}/api/v1/alerts/{alert_id}/workflow-status",
        headers={"x-api-key": api_key, "content-type": "application/json"},
        json=body,
        timeout=TIMEOUT_SECONDS,
    )


def report(response: requests.Response, alert_id: str, record: dict) -> int:
    """Log the outcome, fold the server's answer into the run record."""
    record["http_status"] = response.status_code
    # Apollo stamps every response with this, success included. It is the
    # handle for finding this exact call in the service logs later, so it is
    # recorded whatever the outcome, not just on the error paths.
    record["correlation_id"] = response.headers.get("x-correlation-id")
    log.debug("Response headers: %s", dict(response.headers))

    try:
        payload = response.json()
    except ValueError:
        record["ok"] = False
        record["error"] = "non-JSON response body"
        log.error("HTTP %s, non-JSON body: %s", response.status_code, response.text[:2000])
        return 1

    record["response"] = payload

    if response.status_code == 200:
        record["ok"] = True
        record["changed"] = payload.get("changed")
        record["previous_status"] = payload.get("previous_status")
        record["status"] = payload.get("status")
        if payload.get("changed"):
            log.info("%s: %s -> %s", alert_id, payload.get("previous_status"), payload.get("status"))
        else:
            # The endpoint is an update, not a state machine: setting the status
            # the alert already holds writes nothing and is not an error.
            log.info("%s: already %s, nothing written", alert_id, payload.get("status"))
        log.debug("Response payload: %s", json.dumps(payload, indent=2))
        log.debug("Correlation id: %s", record.get("correlation_id"))
        return 0

    # 400 status_invalid, 401 bad key, 403 key_not_tenant_bound,
    # 404 alert_not_found, 409 status_conflict.
    record["ok"] = False
    record["error_code"] = payload.get("code")
    record["correlation_id"] = payload.get("correlationId") or record.get("correlation_id")
    log.error(
        "HTTP %s (%s): %s",
        response.status_code,
        payload.get("code", "no code"),
        payload.get("message", response.text[:500]),
    )
    log.debug("Error payload: %s", json.dumps(payload, indent=2))
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alert-id", default=DEFAULT_ALERT_ID)
    parser.add_argument("--env", choices=sorted(BASE_URL), default="production")
    parser.add_argument("--status", choices=WORKFLOW_STATUSES, default=DEFAULT_STATUS)
    parser.add_argument("--reason", default=DEFAULT_REASON)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the request that would be sent and exit without sending it.",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="Console verbosity. The file log is always DEBUG.",
    )
    parser.add_argument(
        "--log-dir", type=Path, default=LOG_DIR, help=f"Default: {LOG_DIR}"
    )
    args = parser.parse_args()

    run_id = uuid.uuid4().hex[:8]
    bind_run_id(run_id)
    setup_logging(args.log_level, args.log_dir)

    api_key = load_api_key()
    base_url = BASE_URL[args.env]
    key_fp = fingerprint(api_key)

    record = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "env": args.env,
        "alert_id": args.alert_id,
        "requested_status": args.status,
        "reason": args.reason,
        "key_fingerprint": f"sha256:{key_fp}",
        "dry_run": args.dry_run,
    }

    log.info("run:    %s", run_id)
    log.info("env:    %s", args.env)
    log.info("key:    %s (sha256:%s)", API_KEY_VAR, key_fp)
    log.info(
        "target: PATCH %s/api/v1/alerts/%s/workflow-status", base_url, args.alert_id
    )
    log.info("body:   %s", json.dumps({"status": args.status, "reason": args.reason}))

    if args.dry_run:
        log.info("dry run, nothing sent")
        record["ok"] = True
        record["elapsed_ms"] = 0
        write_run_record(args.log_dir, record)
        return 0

    started = time.monotonic()
    try:
        response = set_workflow_status(
            base_url, api_key, args.alert_id, args.status, args.reason
        )
    except requests.RequestException as err:
        record["ok"] = False
        record["error"] = str(err)
        record["elapsed_ms"] = round((time.monotonic() - started) * 1000)
        log.error("Request failed after %s ms: %s", record["elapsed_ms"], err)
        write_run_record(args.log_dir, record)
        return 1

    record["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    log.debug("Round trip: %s ms", record["elapsed_ms"])
    exit_code = report(response, args.alert_id, record)
    write_run_record(args.log_dir, record)
    log.debug("Run record appended to %s", args.log_dir / RUN_LOG.name)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
