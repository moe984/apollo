#!/usr/bin/env python3
"""
Auto-close an Apollo alert from SOAR.

    PATCH https://apollo.cosmos.tekstream.com/api/v1/alerts/{alertId}/workflow-status
    x-api-key: <the same key SOAR uses for POST /api/v1/ingest/soar>
    {"status": "auto_closed", "reason": "..."}

    200 -> {"alert_id": "...", "changed": true, "previous_status": "open",
            "status": "auto_closed", "changed_at": "...", "changed_by": "apikey:...",
            "reason": "..."}

alert_id is the APOLLO ALERT ID: the Alert Queue's Container ID column value,
also in the alert page URL (/dashboard/alerts/mccrary-7802). It is NOT the bare
SOAR container id that GET /api/v1/dedup/verdict/{containerId} takes.

Errors: 400 bad status, 401 bad key, 403 key not tenant bound,
404 no such alert in this key's scope, 409 an analyst changed it mid-flight (retry).

Setting a status the alert already holds is a 200 no-op with changed=false.
The engine status of the alert (ingested / normalized / deduplicated /
pending_review) is never touched by this call.

Usage:
    export APOLLO_API_KEY=...
    python3 soar_set_workflow_status.py mccrary-7802
    python3 soar_set_workflow_status.py mccrary-7802 "Duplicate of njit-231940"
"""

import os
import sys

import requests

API_KEY = os.environ["APOLLO_API_KEY"]
BASE_URL = "https://apollo.cosmos.tekstream.com"      # dev: https://apollo-dev.cosmos.tekstream.com
DEFAULT_REASON = "Auto-closed by SOAR."


def auto_close(alert_id, reason=DEFAULT_REASON):
    """Set the alert's workflow status to auto_closed. Returns the 200 body."""
    response = requests.patch(
        "%s/api/v1/alerts/%s/workflow-status" % (BASE_URL, alert_id),
        headers={"x-api-key": API_KEY, "content-type": "application/json"},
        json={"status": "auto_closed", "reason": reason},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


if __name__ == "__main__":
    alert_id = sys.argv[1]
    reason = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_REASON

    result = auto_close(alert_id, reason)

    if result["changed"]:
        print("%s: %s -> %s" % (result["alert_id"], result["previous_status"], result["status"]))
    else:
        print("%s: already %s, nothing written" % (result["alert_id"], result["status"]))
