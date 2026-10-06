"""Rebuild the dashboard's input CSV from the 12-month SOAR export.

The original `mom_container_disposition_90d_searches.csv` (90 days, 27,054
containers) is gone - only the running process had it, in memory. This rebuilds
an equivalent from `soar-containers-12mo-20260923.jsonl.gz`, which supersedes it:
twelve months instead of three, and the same containers.

Column mapping, one line each so the substitution is auditable:

    container_id        <- id
    _time               <- create_time        (ARRIVAL, as the old CSV had it)
    soar_instance       <- server
    container_name      <- name
    container_status    <- status
    closure_disposition <- disposition
    sa_decision         <- sa_decision, label mapped back to the old code
                           (SA_DECISION_CODE)
    sa_decision_owner_role <- CEF sa_decision_owner_role
    tenant_name         <- CEF tenant_name, else the `tenant` column
    rule_name           <- CEF rule_name
    search_name         <- CEF search_name
    Final_Rule_Name     <- rule_name, else recovered from search_name
    Final_Disposition   <- disposition rolled up to four classes

    python3 eda/build_csv.py
"""
from __future__ import annotations

import csv
import gzip
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "soar-containers-12mo-20260923.jsonl.gz"
OUT = HERE.parent / "mom_container_disposition_12mo.csv"

CEF_FIELDS = ("rule_name", "search_name", "tenant_name", "container_name",
              "sa_decision_owner_role")
SEARCH_PREFIX = ("Endpoint - ", "Threat - ", "Access - ", "Network - ",
                 "Identity - ", "Risk - ", "Audit - ")
SEARCH_SUFFIX = (" - Rule Clone", " - Rule")

COLUMNS = ["container_id", "_time", "soar_instance", "container_name", "container_status",
           "tenant_name", "rule_name", "search_name", "Final_Rule_Name",
           "closure_disposition", "Final_Disposition", "sa_decision",
           "sa_decision_owner_role"]

# the export carries display labels; the dashboard keys on the old codes
SA_DECISION_CODE = {
    "Close with Disposition": "close_with_disposition",
    "Escalate to Customer": "escalate",
    "Duplicate": "duplicate",
}


def roll_up(disposition: str) -> str:
    """The four classes the old CSV carried in Final_Disposition."""
    d = (disposition or "").strip()
    if not d or d == "None":
        return ""
    low = d.lower()
    if "benign positive" in low:
        return "Benign Positive"
    if "false positive" in low:
        return "False Positive"
    # the old CSV folded Undetermined in with True Positive
    if "true positive" in low or "undetermined" in low:
        return "True Positive"
    return "Other"


def rule_from_search(search: str) -> str:
    s = (search or "").strip()
    for p in SEARCH_PREFIX:
        s = re.sub(f"^{re.escape(p)}", "", s)
    for suf in SEARCH_SUFFIX:
        s = re.sub(f"{re.escape(suf)}$", "", s)
    return s.strip()


def lift(cef) -> dict:
    """First non-empty value of each wanted field across a container's artifacts."""
    got: dict[str, str] = {}
    for item in (cef or ()):
        obj = item
        if not isinstance(obj, dict):
            try:
                obj = json.loads(item)
            except (ValueError, TypeError):
                continue
            if not isinstance(obj, dict):
                continue
        for k in CEF_FIELDS:
            if k not in got:
                v = obj.get(k)
                if isinstance(v, str) and v.strip() and v.strip() != "None":
                    got[k] = v.strip()
        if len(got) == len(CEF_FIELDS):
            break
    return got


def main() -> None:
    rows = 0
    with gzip.open(SOURCE, "rt") as fh, OUT.open("w", newline="") as out:
        w = csv.DictWriter(out, fieldnames=COLUMNS)
        w.writeheader()
        for line in fh:
            rec = json.loads(line)
            cef = lift(rec.get("CEF") or [])
            rule = cef.get("rule_name", "")
            search = cef.get("search_name", "")
            w.writerow({
                "container_id": rec.get("id") or "",
                "_time": rec.get("create_time") or "",
                "soar_instance": rec.get("server") or "",
                "container_name": cef.get("container_name") or rec.get("name") or "",
                "container_status": rec.get("status") or "",
                "tenant_name": cef.get("tenant_name") or rec.get("tenant") or "",
                "rule_name": rule,
                "search_name": search,
                "Final_Rule_Name": rule or rule_from_search(search),
                "closure_disposition": rec.get("disposition") or "",
                "Final_Disposition": roll_up(rec.get("disposition")),
                "sa_decision": SA_DECISION_CODE.get(rec.get("sa_decision") or "",
                                                    rec.get("sa_decision") or ""),
                "sa_decision_owner_role": cef.get("sa_decision_owner_role", ""),
            })
            rows += 1
    print(f"{rows:,} containers -> {OUT.name}  ({OUT.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
