"""Write the training data to CSV, original and preprocessed.

Two files, so the transformation is auditable end to end:

  original.csv       one row per training container, carrying the ES notable
                     artifact exactly as it arrived - every key, nothing dropped,
                     nothing scrubbed. This is the input before any of our code
                     touches it.

  preprocessed.csv   the same containers after preprocessing: the single text
                     column the shipped model is fitted on, plus the label, the
                     ordering key and the split.

Diffing a row between the two shows precisely what the pipeline removed: the
other three artifact families, the 44 keys in config/preprocessing.json, every
timestamp, and the fields Apollo does not forward.

The text column tracks the shipped release - `--release` names which. v20 is the
default, so `text_cef` here is the 937-field Apollo intersection.

    .venv/bin/python export_training_data.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

import dataset as DS
import dataset12 as D12
import dataset_v20 as V20

HERE = Path(__file__).resolve().parent
OUT = HERE / "training-data"

# (parquet, text column) per release, so the export always matches a real model
RELEASES = {"v20": (V20.OUT, "text_v20")}


def main(release: str = "v20") -> None:
    OUT.mkdir(exist_ok=True)
    path, column = RELEASES[release]
    frame = pd.read_parquet(path)

    # --- preprocessed: exactly what the estimator receives -------------------
    train, test = DS.time_split(frame)
    split = pd.Series("train", index=frame.index)
    split[frame["t"] > train["t"].max()] = "test"
    pre = pd.DataFrame({
        "container_id": frame["container_id"],
        "close_time": frame["t"],
        "split": split,
        "disposition": frame["disposition"],
        "y_human": frame["y_human"],
        "text_cef": frame[column],
    })
    pre.to_csv(OUT / "preprocessed.csv", index=False)

    # --- original: the arrival artifact, untouched ---------------------------
    raw = D12.read_source()
    raw["container_id"] = raw["server"].str.cat(raw["id"].astype(str), sep=":")
    keep = set(frame["container_id"])
    raw = raw[raw["container_id"].isin(keep)]

    rows = []
    for rec in raw.itertuples(index=False):
        notable = next((o for o in D12.artifacts(rec.CEF) if D12.is_notable(o)), None)
        rows.append({
            "container_id": rec.container_id,
            "server": rec.server,
            "id": rec.id,
            "close_time": rec.close_time,
            "disposition": rec.disposition,
            "container_name": rec.name,
            "artifact_count": len(rec.CEF),
            "has_notable": notable is not None,
            "notable_keys": len(notable) if notable else 0,
            # the arrival artifact as it came off the wire, every key, no scrubbing
            "notable_json": json.dumps(notable, sort_keys=True) if notable else "",
        })
    org = pd.DataFrame(rows).merge(
        pre[["container_id", "split", "y_human"]], on="container_id", how="left")
    org = org.set_index("container_id").loc[pre["container_id"]].reset_index()
    org.to_csv(OUT / "original.csv", index=False)

    print(f"release {release}  column {column}")
    print(f"rows {len(pre):,}   not-benign {int(pre.y_human.sum()):,} "
          f"({pre.y_human.mean()*100:.1f}%)")
    print(f"  train {int((pre.split=='train').sum()):,}   test {int((pre.split=='test').sum()):,}")
    print(f"  with an ES notable {int(org.has_notable.sum()):,} "
          f"({org.has_notable.mean()*100:.1f}%)")
    for name in ("original.csv", "preprocessed.csv"):
        mb = (OUT / name).stat().st_size / 1e6
        print(f"  training-data/{name:20s} {mb:7.1f} MB")


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--release", default="v20", choices=sorted(RELEASES))
    main(ap.parse_args().release)
