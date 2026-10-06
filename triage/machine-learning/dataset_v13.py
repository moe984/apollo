"""v13 - v12 with the identity entities removed.

v12 appends nine entity types to the 326 high-coverage CEF fields. Three of them
name a person or a machine:

    EMAIL      c00539322@louisiana.edu
    USERNAME   Administrator, svc.admgmt, c00539322
    MACHINE    win.louisiana.edu, cymulate11

v13 drops those three and keeps the six that describe *what happened* rather than
*who it happened to*:

    IPV4 IPV6  addresses
    URL        links
    FILEPATH FILENAME  files
    HASH       IOCs

The question this answers is whether the model needs identity, or only
behaviour. v12's own numbers say the entities contribute about +0.0002 over the
CEF fields alone, so the expectation is that removing three of them costs almost
nothing - which would mean the identity entities can go for free.

Note the CEF fields still carry usernames and hostnames in their own right
(`destinationUserName` is on 78.9% of alerts). v13 removes the *extracted*
identity entities, not every trace of identity from the input. Removing identity
altogether is a different experiment.

    .venv/bin/python dataset_v13.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

import dataset as DS
import dataset12 as D12
import entities as ENT

HERE = Path(__file__).resolve().parent
OUT = HERE / "dataset_v13.parquet"

# The three v12 labels that name a person or a machine.
IDENTITY = ("EMAIL", "USERNAME", "MACHINE")
LABELS = {k: v for k, v in ENT.LABELS.items() if k not in IDENTITY}
FLOOR = ENT.DEFAULT_FLOOR          # the same 326-key cut v12 ships


def build() -> pd.DataFrame:
    seen, total, notables = ENT.coverage()

    columns = ["container_id"] + [c for c in LABELS.values()]
    ents = pd.read_csv(ENT.ENTITIES, usecols=columns)

    # same renderer as v12, over the reduced label set
    original = ENT.LABELS
    try:
        ENT.LABELS = LABELS
        ents["text_ent"] = ENT.entity_text(ents)
    finally:
        ENT.LABELS = original

    frame = D12.build()
    frame = frame.merge(ents[["container_id", "text_ent"]], on="container_id",
                        how="left", validate="one_to_one")
    frame["text_ent"] = frame["text_ent"].fillna("")

    keep = {k for k, c in seen.items() if c / total * 100 >= FLOOR}
    rendered = {cid: ENT.cef_text(nb, keep) for cid, nb in notables}
    frame["cef"] = frame["container_id"].map(rendered).fillna("")
    frame["text_v13"] = (frame["cef"] + " " + frame["text_ent"]).str.strip()
    frame.attrs["keys"] = len(keep)
    return frame


if __name__ == "__main__":
    f = build()
    f.to_parquet(OUT, index=False)
    train, test = DS.time_split(f)
    print(f"rows {len(f):,}  -> {OUT.name}")
    print(f"CEF keys kept   : {f.attrs['keys']:,} (coverage >= {FLOOR:.0f}%)")
    print(f"entity types    : {', '.join(sorted(LABELS))}")
    print(f"dropped         : {', '.join(IDENTITY)}")
    print(f"median terms    : {int(f.text_v13.str.split().str.len().median()):,}")
    print(f"alerts with >=1 remaining entity: "
          f"{(f.text_ent.str.len()>0).mean()*100:.1f}%")
    print(f"train {len(train):,}  test {len(test):,}")
