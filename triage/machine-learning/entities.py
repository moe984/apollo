"""Entity rendering and field-coverage helpers.

Shared by the releases that append NER output to the CEF text. SecureBERT-NER v2
was run once over every training alert; `training-data/entities.csv` holds every
span it emitted, at every confidence, under every label, so any later decision
about thresholds or which labels to trust is a pandas filter rather than another
GPU hour.

    coverage()     how many alerts carry each CEF key - the input to a floor
    cef_text()     the kept keys of one notable as `key value` text
    entity_text()  each >=0.999 entity as `label value`

`LABELS` here is the full nine. `dataset_v13.LABELS` narrows it to the six that
describe *what happened* rather than *who it happened to*, which is what ships.

USERNAME reads `hi_USERNAME_kept` rather than the raw column: 82.7% of raw
high-confidence USERNAME spans were metadata values - the model reads
`severity: high` and tags "high" as a person. See `ner/username_context.py`.
"""
from __future__ import annotations

import collections
import gzip
import json
import re
from pathlib import Path

import pandas as pd

import dataset as DS
import dataset12 as D12
import notable_text as NT

HERE = Path(__file__).resolve().parent
ENTITIES = HERE / "training-data" / "entities.csv"
USERNAMES = HERE / "training-data" / "username_context.csv"

# Every NER label we trust at >=0.999, and the column each reads from.
LABELS = {
    "IPV4":     "hi_IPV4",
    "IPV6":     "hi_IPV6",
    "MACHINE":  "hi_MACHINE",
    "USERNAME": "hi_USERNAME_kept",      # key-context filtered, not the raw column
    "EMAIL":    "hi_EMAIL",
    "URL":      "hi_URL",
    "FILEPATH": "hi_FILEPATH",
    "FILENAME": "hi_FILENAME",
    "HASH":     "hi_HASH",
}

# Coverage floors to compare. The shipped one is chosen by measurement.
FLOORS = (1.0, 10.0, 50.0)
DEFAULT_FLOOR = 1.0

DROP = NT.DROP     # the shared 44, from config/preprocessing.json
NOT_A_VALUE = ("none", "null", "-", "unknown")
SLUG = re.compile(r"[^a-z0-9]+")


def slug(value: str) -> str:
    """An entity value as one TF-IDF token - `10.1.2.3` would otherwise shatter."""
    return SLUG.sub("_", value.strip().lower()).strip("_")


def coverage() -> tuple[collections.Counter, int, list]:
    """How many labelled alerts carry each key, and the notables themselves."""
    seen = collections.Counter()
    notables = []
    total = 0
    with gzip.open(D12.SOURCE, "rt") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec.get("disposition") not in D12.KEEP:
                continue
            notable = next((o for o in D12.artifacts(rec.get("CEF") or [])
                            if D12.is_notable(o)), None)
            notables.append((f"{rec['server']}:{rec['id']}", notable))
            if notable is None:
                continue
            total += 1
            for key in notable:
                if key in DROP:
                    continue
                value = D12.scrub_time(D12.flatten(notable[key])).strip()
                if value and value.lower() not in NOT_A_VALUE:
                    seen[key] += 1
    return seen, total, notables


def cef_text(notable: dict | None, keep: set[str]) -> str:
    """The kept keys of one notable as `key value` text."""
    if not notable:
        return ""
    parts = []
    for key in sorted(notable):
        if key in DROP or key not in keep:
            continue
        value = D12.scrub_time(D12.flatten(notable[key])).strip()
        if value and value.lower() not in NOT_A_VALUE:
            parts.append(f"{key} {value}")
    return " ".join(parts).lower()


def entity_text(frame: pd.DataFrame) -> pd.Series:
    """Each >=0.999 entity as `label value` - the type, then what was found.

        ipv4 10.196.116.129  ipv4 10.196.101.130  machine win.louisiana.edu
        username c00539322   email c00539322@louisiana.edu

    The type is its own token, so term frequency carries "this alert mentions six
    addresses", and the word bigram binds the type to its value the same way the
    CEF `key value` pairs work.

    Four encodings were measured and all landed within 0.0002 ROC of each other -
    `label value`, `label_value`, `label label_value`, and the slugged variants.
    This one is the most readable, so it is the one that ships.
    """
    out = []
    for row in frame.itertuples(index=False):
        record = row._asdict()
        parts = []
        for label, column in LABELS.items():
            cell = record.get(column)
            if not isinstance(cell, str) or not cell.strip():
                continue
            tag = label.lower()
            for value in cell.split("|"):
                value = value.strip().lower()
                if value:
                    parts.append(f"{tag} {value}")
        out.append(" ".join(parts))
    return pd.Series(out, index=frame.index)



