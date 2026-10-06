"""Recover the field each USERNAME span came from, and keep only the identity ones.

USERNAME is the noisiest of the nine labels: 84,339 alert-occurrences at >=0.999,
and the four commonest values are `user`, `medium`, `high` and `low`. Those are
not NER mistakes in the ordinary sense - the model is reading

    severity: high
    urgency: medium
    user_risk_object_type: user

and tagging the value of a *metadata* field as a person. On its own training
distribution this model scores 0.986 precision; the failure is that we feed it a
flattened alert, where a severity word sits in the same grammatical slot a
username would.

`local_ner.render` lays the notable out as one `key: value` line per field, so
every span's character offset identifies the line it sits on, and the text before
the first colon is the key. That key is the fix: accept a USERNAME only when it
was read out of a field that names an account.

The rule is token-based, not substring-based - `accountName` must not be rejected
for containing "count", and `destinationUserName` must split on the capitals.

    python3 username_context.py        # writes training-data/username_context.csv
"""
from __future__ import annotations

import bisect
import collections
import csv
import json
import re
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "machine-learning" / "training-data"
OUT = DATA / "username_context.csv"

# `render` drops these before laying the notable out, so line offsets only line
# up if the same set is dropped here. Mirrored from local_ner rather than
# imported, so this script does not need torch.
SKIP = {"_bkt", "_cd", "_si", "_indextime", "_sourcetype", "_time", "_eventtype_color",
        "event_id", "rule_id", "orig_sid", "orig_rid", "source_event_id", "source_guid",
        "info_max_time", "info_min_time", "info_search_time", "timeendpos", "timestartpos",
        "investigation_profiles", "extract_artifacts", "contributing_events_search"}

# Separators, or the seam inside camelCase. `[^A-Za-z0-9]+` must include the
# uppercase range or the first alternative swallows the capital that the second
# alternative exists to split on.
SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z])(?=[A-Z])")

# A field whose value is a person or an account.
NAMEY = {"user", "users", "username", "account", "accountname", "actor", "caller",
         "principal", "upn", "entity", "sid", "duser", "object", "owner"}
# ...unless the field is about that account rather than being it. `user_priority`
# holds "medium"; `user_risk_object_type` holds "user"; `user_identity_tag` holds
# "student". Every one of these is a real field whose value is a category.
NOT_A_NAME = {"type", "category", "priority", "priorities", "severity", "severities",
              "urgency", "country", "city", "state", "dept", "department", "title",
              "error", "count", "score", "id", "first", "last", "bunit", "watchlist",
              "email", "domain", "status", "time", "date", "tag", "role", "realname",
              "nick", "field", "length", "hash"}


def tokens(key: str) -> set[str]:
    return {t.lower() for t in SPLIT.split(key) if t}


def is_identity_field(key: str) -> bool:
    t = tokens(key)
    return bool(t & NAMEY) and not (t & NOT_A_NAME)


def render(notable: dict) -> str:
    """Byte-for-byte what the extractor fed the model."""
    return "\n".join(f"{k}: {v}" for k, v in sorted(notable.items()) if k not in SKIP)


def line_starts(text: str) -> list[int]:
    return [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n"]


def key_at(text: str, starts: list[int], offset: int) -> str:
    i = bisect.bisect_right(starts, offset) - 1
    end = text.find("\n", starts[i])
    line = text[starts[i]: end if end != -1 else len(text)]
    return line.split(":", 1)[0]


def main() -> None:
    orig = pd.read_csv(DATA / "original.csv", usecols=["container_id", "notable_json"])
    ents = pd.read_csv(DATA / "entities.csv", usecols=["container_id", "entities_hi"])
    frame = orig.merge(ents, on="container_id", how="inner", validate="one_to_one")

    tally = collections.Counter()
    kept_spans = dropped_spans = 0
    with OUT.open("w", newline="") as fh:
        out = csv.DictWriter(fh, ["container_id", "hi_USERNAME_kept",
                                  "hi_USERNAME_dropped", "n_kept", "n_dropped"])
        out.writeheader()
        for cid, notable_json, hi in frame.itertuples(index=False):
            keep, drop = [], []
            if isinstance(hi, str) and "USERNAME" in hi and isinstance(notable_json, str) \
                    and notable_json:
                notable = json.loads(notable_json)
                text = render(notable)
                starts = line_starts(text)
                for span in json.loads(hi):
                    if span["label"] != "USERNAME":
                        continue
                    key = key_at(text, starts, span["start"])
                    good = is_identity_field(key)
                    tally[(key, good)] += 1
                    (keep if good else drop).append(span["value"])
            kept_spans += len(keep)
            dropped_spans += len(drop)
            out.writerow({"container_id": cid,
                          "hi_USERNAME_kept": "|".join(dict.fromkeys(keep)),
                          "hi_USERNAME_dropped": "|".join(dict.fromkeys(drop)),
                          "n_kept": len(keep), "n_dropped": len(drop)})

    total = kept_spans + dropped_spans
    print(f"USERNAME spans at >=0.999: {total:,}   "
          f"kept {kept_spans:,} ({kept_spans/total*100:.1f}%)   dropped {dropped_spans:,}")
    print(f"-> {OUT.relative_to(HERE.parent)}\n")
    fields = collections.Counter()
    for (key, good), n in tally.items():
        fields[(key, good)] = n
    print(f"{'field':32s} {'spans':>7}  verdict")
    for (key, good), n in fields.most_common(20):
        print(f"{key:32s} {n:7,}  {'identity' if good else 'metadata'}")


if __name__ == "__main__":
    sys.exit(main())
