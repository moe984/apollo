"""Build the routing dataset from the 12-month SOAR container extract.

The 90-day CSV gave 10,638 verdicts over three months. This extract gives 32,429
over twelve, across three SOAR servers, and it carries two things the CSV could
not:

  * **`close_time`** - the moment a human actually ruled the container. The CSV's
    `_time` is *arrival*; we proved it (duplicate profile vs worked-hour profile,
    r = 0.816). Every forward split until now was ordered by when an alert showed
    up rather than when it was decided. This one is ordered by the decision, so
    "prior-only" finally means what it says.
  * **`(server, id)`** - `id` repeats across the three servers, so it is not a
    key on its own. 123,897 containers carry only 104,628 distinct ids.

The detection identifiers live inside the `CEF` artifact array rather than in
columns of their own, so they are lifted out here: the first non-empty value
across a container's artifacts, which recovers `rule_name`, `search_name` and
`tenant_name` on ~100% of rows.

Everything downstream of that is `dataset.py`: this module only translates the
extract into the vocabulary that `dataset.build` already speaks, so there is one
implementation of the prior-only feature rules, not two.

    python3 dataset12.py            # writes dataset12.parquet + a summary
"""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

import cefconfig as CFG
import dataset as DS

HERE = Path(__file__).resolve().parent
SOURCE = Path(__file__).resolve().parent.parent / "soar-containers-12mo-20260923.jsonl.gz"
OUT = HERE / "dataset12.parquet"

# The four verdicts a human can reach. `Other` is the heartbeat/duplicate bucket,
# `Undetermined` is an analyst who could not rule it, and a null was never
# dispositioned - none of the three is a label.
KEEP = {
    "True Positive - Suspicious Activity": "True Positive",
    "False Positive - Incorrect Analytic Logic": "False Positive",
    "False Positive - Inaccurate Data": "False Positive",
    "Benign Positive - Suspicious But Expected": "Benign Positive",
}
# Identity: what the alert *is*. Set at ingest, repeated on every artifact, so
# any artifact may supply it.
CEF_FIELDS = ("rule_name", "search_name", "tenant_name", "container_name")

# Whole-notable text. Splunk plumbing and per-event identifiers are dropped: a
# bucket id or a search id is unique per alert, so it can only ever be noise or
# a memorised handle on one row, never a pattern that transfers.
# Loaded from config/preprocessing.json so training and serving read one file.
# See cefconfig.py; `freeze.py` records the config's sha256 in every manifest.
CEF_DROP = CFG.DROP

# `search_name` is the saved search; the rule it wraps is the middle of it.
SEARCH_PREFIX = ("Endpoint - ", "Threat - ", "Access - ", "Network - ",
                 "Identity - ", "Risk - ", "Audit - ")
SEARCH_SUFFIX = (" - Rule Clone", " - Rule")


def artifacts(arr):
    """Yield a container's CEF artifacts as dicts.

    The JSONL carries them as objects; the parquet export carried them as JSON
    strings. Accepting both keeps this readable from either.
    """
    for item in (arr if arr is not None else ()):
        if isinstance(item, dict):
            yield item
            continue
        try:
            obj = json.loads(item)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            yield obj


def read_source(path: Path = SOURCE) -> pd.DataFrame:
    """Every container that carries a usable verdict, straight off the JSONL.

    Filtering on the disposition while streaming keeps 32,429 of 123,897 records
    in memory instead of all of them - the CEF arrays are the bulk of the file.
    """
    rows = []
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec.get("disposition") in KEEP:
                rows.append({"server": rec["server"], "id": rec["id"],
                             "name": rec.get("name"),
                             "disposition": rec["disposition"],
                             "close_time": rec.get("close_time"),
                             "CEF": rec.get("CEF") or []})
    return pd.DataFrame(rows)


def lift_cef(arrays) -> pd.DataFrame:
    """First non-empty value of each wanted field across a container's artifacts.

    A container carries a handful of artifacts and the detection identifiers are
    repeated on each, so the first one that has a value is the value.
    """
    out = {k: [] for k in CEF_FIELDS}
    for arr in arrays:
        got: dict[str, str] = {}
        for obj in artifacts(arr):
            for k in CEF_FIELDS:
                if k not in got:
                    v = obj.get(k)
                    if isinstance(v, str) and v.strip():
                        got[k] = v.strip()
            if len(got) == len(CEF_FIELDS):
                break
        for k in CEF_FIELDS:
            out[k].append(got.get(k))
    return pd.DataFrame(out)


def is_notable(obj: dict) -> bool:
    """True for the Splunk ES notable that opened the container.

    A container carries three families of artifact and only this one predates the
    analyst:

      * the **ES notable** - 59 keys, `rule_id` among them, no `soar_event`. The
        alert as it arrived.
      * a **settings/metrics** artifact - no `soar_event` either, but it carries
        `SA_DISPOSITION`, which *is* the label, and `CLOSURE_TIME`.
      * **`soar_event=*`** artifacts - `incident_closure`, `sa_decision`,
        `escalation_receipt` - written as the container is worked.

    There is also a Slack/escalation notification artifact (`reply_count`,
    `snooze_count`, `botherbot_customer_replied`) that has no `soar_event` key.
    It only exists when an alert was escalated to the customer, which is close to
    a definition of the human lane, so anything read from it leaks.

    Absence of `soar_event` is therefore NOT a test for "arrived before the
    verdict". `rule_id` present and `SA_DISPOSITION` absent is.
    """
    return ("soar_event" not in obj and "SA_DISPOSITION" not in obj
            and "rule_id" in obj)


# Anything that encodes *when*. The blob carries timestamps in at least twenty
# fields (user_startDate, firstSeen, loginTime, drilldown_earliest ...) and dates
# embedded inside filenames and rule descriptions, so this scrubs values rather
# than dropping keys.
#
# It has to go. The human-lane base rate runs from 6.5% in 2025-11 to 25.3% in
# 2026-09, so a date is genuinely predictive *of the training window* and of
# nothing beyond it - the first fit put "2026 04" among its strongest human-lane
# features. That is the same reason the tabular model has no hour and no weekday.
TIME_TOKEN = CFG.TIME_TOKEN


def scrub_time(text: str) -> str:
    return TIME_TOKEN.sub(" ", text)


def flatten(value) -> str:
    """A CEF value as a single space-joined string."""
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    return "" if value is None else str(value)


def cef_text(arrays) -> list[str]:
    """Every field of the ES notable as one bag of text, `key value` per pair.

    Pairs rather than bare values so a word bigram can bind a field to its
    reading - "severity low" and "urgency low" stay distinguishable, which they
    would not if the keys were dropped.
    """
    out = []
    for arr in arrays:
        parts = []
        for obj in artifacts(arr):
            if not is_notable(obj):
                continue
            for k in sorted(obj):
                if k in CEF_DROP:
                    continue
                v = scrub_time(flatten(obj[k])).strip()
                if v and v.lower() not in ("none", "null", "-", "unknown"):
                    parts.append(f"{k} {v}")
            break                      # one notable per container
        out.append(" ".join(parts).lower())
    return out


def rule_from_search(search: pd.Series) -> pd.Series:
    """Recover the rule a saved search wraps, the way `Final_Rule_Name` does in
    the CSV: drop the leading security domain and the trailing " - Rule"."""
    s = search.fillna("").str.strip()
    for p in SEARCH_PREFIX:
        s = s.str.replace(f"^{p}", "", regex=True)
    for suf in SEARCH_SUFFIX:
        s = s.str.replace(f"{suf}$", "", regex=True)
    return s.str.strip()


def normalise() -> pd.DataFrame:
    """The extract, in the column vocabulary `dataset.build` expects."""
    v = read_source()
    cef = lift_cef(v["CEF"].tolist())
    cef.index = v.index

    # a literal "None" reaches the artifact as a string; the CSV loader maps it
    # to the same placeholder as an empty field, so do the same here
    cef = cef.replace({"None": None, "": None})

    df = pd.DataFrame(index=v.index)
    # the decision time, not the arrival time - the whole point of this source
    df["t"] = pd.to_datetime(v["close_time"], utc=True)
    # `id` is server-local and so is `tenant`: id 4 is Richline on one server and
    # LSU_HSC_NO on another. Only the server-qualified id and the artifact's own
    # tenant_name are stable keys.
    df["container_id"] = v["server"].str.cat(v["id"].astype(str), sep=":")
    df["container_name"] = cef["container_name"].fillna(v["name"]).fillna("(unnamed)")
    df["tenant_name"] = cef["tenant_name"].fillna("(unknown)")
    df["search_name"] = cef["search_name"].fillna("(unattributed)")

    rule = cef["rule_name"]
    df["rule_name"] = rule.fillna("(unattributed)")
    # Final_Rule_Name: the rule if the artifact named one, else recovered from
    # the saved search. 5.6% of the CSV needed that fallback.
    derived = rule_from_search(cef["search_name"])
    df[DS.RULE] = rule.fillna(derived).replace("", np.nan).fillna("(unattributed)")

    df["cef_blob"] = cef_text(v["CEF"].tolist())

    df["Final_Disposition"] = v["disposition"].map(KEEP)
    return df.dropna(subset=["t"]).sort_values("t").reset_index(drop=True)


def build() -> pd.DataFrame:
    """The shared feature table, plus the notable blob the router reads.

    `dataset.build` owns every prior-only feature. The blob is neither prior-only
    nor derived - it is the alert itself, known the moment it arrives - so it is
    attached afterwards rather than threaded through.
    """
    norm = normalise()
    f = DS.build(norm)
    f = f.merge(norm[["container_id", "cef_blob"]], on="container_id",
                how="left", validate="one_to_one")
    f["text_cef"] = f.pop("cef_blob").fillna("").astype(str)
    return f


if __name__ == "__main__":
    f = build()
    f.to_parquet(OUT, index=False)
    tr, te = DS.time_split(f)
    print(f"rows {len(f):,}   features {len(DS.feature_names(f))}   -> {OUT.name}")
    print(f"human lane {f.y_human.sum():,} ({f.y_human.mean()*100:.1f}%)   "
          f"AI lane {(1-f.y_human).sum():,} ({(1-f.y_human).mean()*100:.1f}%)")
    print(f"span  {f.t.min():%Y-%m-%d} -> {f.t.max():%Y-%m-%d}   "
          f"rules {f.rule.nunique()}   tenants {f.tenant.nunique()}")
    print(f"train {len(tr):,} rows to {tr.t.max():%Y-%m-%d} "
          f"({tr.y_human.mean()*100:.1f}% human)")
    print(f"test  {len(te):,} rows from {te.t.min():%Y-%m-%d} "
          f"({te.y_human.mean()*100:.1f}% human)")
