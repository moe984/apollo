"""The notable -> text rule, and the Apollo alignment that makes it servable.

This module owns the rule that turns an ES notable into the one string the model
reads, and the two-key adjustment that makes that string identical on the serving
side. `ml/v18` is fitted on its output; `ml/v20` narrows it further.

Apollo forwards the ES notable itself, in `soar_cef`, not a summary of it. On
7,032 validated joins the string built from Apollo is **byte-identical** to the
string built from the SOAR export once two keys are excluded:

    extract_artifacts   SOAR has it, Apollo never sends it. 0.89% of training
                        tokens, and a near-constant Splunk config blob.
    splunk_query        Apollo has it on 46% of records, the SOAR export does
                        not. Nothing to train on, so it is ignored at inference.

Both live in the `serving_alignment` group of `config/preprocessing.json`.
Leaving them in makes the same alert score differently depending on which side
built the string - mean 0.042, max 0.38, against a zero-miss threshold of 0.0014.

`ml/v20` narrows this further to the 937 fields Apollo has actually been observed
to forward; see `dataset_v20.py`.

    .venv/bin/python notable_text.py
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

import pandas as pd

import cefconfig as CFG
import dataset as DS
import dataset12 as D12

HERE = Path(__file__).resolve().parent
OUT = HERE / "notable_text.parquet"
APOLLO_EXPORT = Path("/Users/mohammad.yekrangian/Downloads/"
                     "apollo-soar-alerts-20260923.jsonl.gz")

# The drop set lives in config/preprocessing.json, where the two asymmetric keys
# sit in their own `serving_alignment` group alongside the reason.
ASYMMETRIC = frozenset(
    CFG.CONFIG["drop_keys"]["serving_alignment"]["keys"])
DROP = CFG.DROP


def notable_of(arr):
    for obj in D12.artifacts(arr):
        if D12.is_notable(obj):
            return obj
    return None


def to_text(notable: dict | None, drop=DROP, keep: set[str] | None = None) -> str:
    """The notable as `key value` text - v9's rule, with a different drop set.

    `keep`, when given, is an allow-list: only keys Apollo was observed to send.
    """
    if not notable:
        return ""
    parts = []
    for key in sorted(notable):
        if key in drop or (keep is not None and key not in keep):
            continue
        value = D12.scrub_time(D12.flatten(notable[key])).strip()
        if value and value.lower() not in ("none", "null", "-", "unknown"):
            parts.append(f"{key} {value}")
    return " ".join(parts).lower()


def apollo_notables(path: Path = APOLLO_EXPORT) -> dict[str, dict]:
    """`container_id` -> the ES notable Apollo forwarded for it."""
    out = {}
    with gzip.open(path, "rt") as fh:
        for line in fh:
            rec = json.loads(line)
            cef = rec.get("soar_cef") or []
            cid = (rec.get("result") or {}).get("container_id")
            if cid is not None and cef and isinstance(cef[0], dict):
                out[f"prod-soar2:{cid}"] = cef[0]
    return out


def observed_keys(live: dict[str, dict]) -> set[str]:
    keys: set[str] = set()
    for notable in live.values():
        keys.update(notable)
    return keys


def build() -> pd.DataFrame:
    """v9's table plus the two v11 inputs and the live Apollo string.

    `apollo_valid` marks rows where Apollo's record provably describes the same
    alert (`source_event_id` agrees). The container id spaces only converged in
    2026-09; before that a join is silently wrong, so it must be validated.
    """
    frame = D12.build()
    live = apollo_notables()
    strict = observed_keys(live)

    rows = []
    with gzip.open(D12.SOURCE, "rt") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec.get("disposition") not in D12.KEEP:
                continue
            cid = f"{rec['server']}:{rec['id']}"
            notable = notable_of(rec.get("CEF") or [])
            sent = live.get(cid)
            valid = bool(sent) and notable is not None and (
                str(sent.get("source_event_id") or "")
                == str(notable.get("source_event_id") or ""))
            rows.append({
                "container_id": cid,
                "text_v18":        to_text(notable),
                "text_v11_strict": to_text(notable, keep=strict),
                # the same alert rendered from what Apollo really sent
                "text_live":       to_text(sent) if valid else "",
                "apollo_valid":    valid,
            })

    return frame.merge(pd.DataFrame(rows), on="container_id", how="left",
                       validate="one_to_one")


if __name__ == "__main__":
    f = build()
    f.to_parquet(OUT, index=False)
    train, test = DS.time_split(f)
    print(f"rows {len(f):,}  -> {OUT.name}")
    for col in ("text_cef", "text_v18", "text_v11_strict"):
        print(f"  {col:18s} median terms "
              f"{int(f[col].str.split().str.len().median()):4,}")
    live = f[f.apollo_valid]
    same = (live["text_v18"] == live["text_live"]).mean() * 100
    print(f"\nvalidated live Apollo rows: {len(live):,}")
    print(f"  text_v18 identical to what Apollo sent: {same:.1f}%")
    print(f"  of those in the test window: "
          f"{int(live.container_id.isin(test.container_id).sum()):,}")
