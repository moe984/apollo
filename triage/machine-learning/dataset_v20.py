"""v20 - trained only on the fields Apollo actually sends.

v18 fits all 1,312 keys the training corpus contains. 375 of those have never
appeared in an Apollo record, so at inference the model carries weights for terms
that will never arrive. Apollo is the reality: it is what feeds the model in
production, so it should decide what the model is fitted on.

The intersection is computed from the Apollo export rather than assumed:

    Apollo        13,316 alerts   982 distinct keys
    training      30,830 alerts 1,312 distinct keys
    COMMON                        937 keys  - 98.7% of training terms
    training only                 375 keys  -  1.3%
    Apollo only                    45 keys  - reach the service today and
                                              contribute nothing, because the
                                              vocabulary has no terms for them

The 375 dropped are mostly VirusTotal enrichment and vendor-specific fields, each
worth well under 0.2% of terms:

    vt_collections_names 0.172%   vt_whois 0.104%   vt_collections 0.052%
    munge_alertID        0.052%   file path 0.048%  userSid        0.035%

Measured out-of-fold over three seeds, against v18:

    ROC              0.8541 -> 0.8540   (-0.0001, the noise floor)
    PR               0.6900 -> 0.6893
    auto-closed @ 0 misses  1.50% -> 1.76%

Free, and better at the operating point.

**The caveat.** The Apollo export is one server (prod-soar2) over two months, so
a key absent from it may be absent from the *sample* rather than from the feed.
Checked: only 20 of the 375 occur on prod-soar2 inside Apollo's own window, and
19 of those on 8-9 alerts each. Small, but this list should be recomputed from a
fresh export at each retraining rather than pinned.

    .venv/bin/python dataset_v20.py
"""
from __future__ import annotations

import collections
import gzip
import json
from pathlib import Path

import pandas as pd

import dataset as DS
import dataset12 as D12
import notable_text as NT

HERE = Path(__file__).resolve().parent
OUT = HERE / "dataset_v20.parquet"
KEYS_FILE = HERE / "config" / "apollo_keys.json"
APOLLO_EXPORT = Path("/Users/mohammad.yekrangian/Downloads/"
                     "apollo-soar-alerts-20260923.jsonl.gz")


def apollo_keys(path: Path = APOLLO_EXPORT) -> set[str]:
    """Every key Apollo has been observed to forward, less the shared drop set."""
    seen: set[str] = set()
    with gzip.open(path, "rt") as fh:
        for line in fh:
            cef = json.loads(line).get("soar_cef") or []
            if cef and isinstance(cef[0], dict):
                seen.update(k for k in cef[0] if k not in NT.DROP)
    return seen


def training_keys() -> collections.Counter:
    seen: collections.Counter = collections.Counter()
    with gzip.open(D12.SOURCE, "rt") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec.get("disposition") not in D12.KEEP:
                continue
            notable = next((o for o in D12.artifacts(rec.get("CEF") or [])
                            if D12.is_notable(o)), None)
            if notable is None:
                continue
            for key in notable:
                if key in NT.DROP:
                    continue
                value = D12.scrub_time(D12.flatten(notable[key])).strip()
                if value and value.lower() not in ("none", "null", "-", "unknown"):
                    seen[key] += 1
    return seen


def keep_keys(write: bool = True) -> set[str]:
    """The intersection, cached to config/apollo_keys.json for the serving side."""
    common = sorted(set(training_keys()) & apollo_keys())
    if write:
        KEYS_FILE.parent.mkdir(exist_ok=True)
        KEYS_FILE.write_text(json.dumps(
            {"note": ("Keys observed in both the Apollo feed and the training "
                      "corpus. v20 fits only these. Recompute from a fresh Apollo "
                      "export at every retraining - see dataset_v20.py."),
             "apollo_export": APOLLO_EXPORT.name,
             "n_keys": len(common), "keys": common}, indent=2) + "\n")
    return set(common)


def to_text(notable: dict | None, keep: set[str]) -> str:
    if not notable:
        return ""
    parts = []
    for key in sorted(notable):
        if key in NT.DROP or key not in keep:
            continue
        value = D12.scrub_time(D12.flatten(notable[key])).strip()
        if value and value.lower() not in ("none", "null", "-", "unknown"):
            parts.append(f"{key} {value}")
    return " ".join(parts).lower()


def build() -> pd.DataFrame:
    keep = keep_keys()
    rendered = {}
    with gzip.open(D12.SOURCE, "rt") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec.get("disposition") not in D12.KEEP:
                continue
            notable = next((o for o in D12.artifacts(rec.get("CEF") or [])
                            if D12.is_notable(o)), None)
            rendered[f"{rec['server']}:{rec['id']}"] = to_text(notable, keep)

    frame = D12.build()
    frame["text_v20"] = frame["container_id"].map(rendered).fillna("")
    frame.attrs["n_keys"] = len(keep)
    return frame


if __name__ == "__main__":
    f = build()
    f.to_parquet(OUT, index=False)
    train, test = DS.time_split(f)
    print(f"rows {len(f):,}  -> {OUT.name}")
    print(f"keys fitted  : {f.attrs['n_keys']:,}  (of 1,312 in the corpus)")
    print(f"key list     : {KEYS_FILE.relative_to(HERE)}")
    print(f"median terms : {int(f.text_v20.str.split().str.len().median()):,}")
    print(f"train {len(train):,}  test {len(test):,}")
