"""Freeze a trained router into an immutable, auditable release.

Two kinds of release, because a threshold only means something next to the data
it was measured on:

  **holdout** - the exact estimator that produced the numbers in its own
  manifest, trained on the first 70% and measured on the 30% it never saw. Use it
  when the point is the evaluation.

  **--full** - trained on every row, for deployment. It has no held-out window
  left, so its thresholds come from out-of-fold scores over five expanding
  forward folds: each block is scored by a model fitted only on what came before
  it. Same estimator shape, same data, nothing scored by a model that saw it.

`predictions_sha256` fingerprints the scores either way, so anyone can prove the
file they hold is the file that was frozen.

    .venv/bin/python freeze.py            # holdout release: the evaluated model
    .venv/bin/python freeze.py --full     # deployment release: trained on everything
    .venv/bin/python freeze.py --verify   # re-scores every frozen release
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.metrics import average_precision_score, roc_auc_score

import cefconfig as CFG
import dataset as DS
import dataset12 as D12
import metrics as MX
import notable_text as NT
import entities as ENT
import dataset_v13 as V13
import dataset_v20 as V20
import sgd_model as SM

# Each input column is a different claim about what the alert *is*, so the
# prose that ships in the manifest travels with the column rather than being
# written once and going stale when the column changes.
STACKS = {
    "text_v18": (
        "TF-IDF over the whole Splunk ES notable exactly as Apollo forwards it, "
        "into an averaged SGDClassifier. Identical to v11 but for min_df=1, "
        "which keeps terms seen once: +0.0018 ROC and 56% more coverage at the "
        "zero-miss operating point",
        "the whole ES notable artifact, every field as 'key value' text "
        "(word 1-2gram, min_df=1, sublinear_tf); timestamps, epochs and bare "
        "years scrubbed; extract_artifacts and splunk_query dropped because "
        "Apollo and the SOAR export disagree on them"),
    "text_v20": (
        "TF-IDF over the 937 ES notable fields Apollo has actually been observed "
        "to forward - the intersection of the training corpus and the live feed - "
        "into an averaged SGDClassifier. The model is fitted on exactly what it "
        "will be served, and nothing it will never receive",
        "the 937 ES notable keys present in both the training corpus and the "
        "Apollo export, each as 'key value' text (word 1-2gram, min_df=1, "
        "sublinear_tf); timestamps, epochs and bare years scrubbed; the shared "
        "44-key drop set applied first"),
    "text_v13": (
        "TF-IDF over the ES notable's high-coverage fields - every key on at "
        "least 1% of alerts, 326 of them - plus the six NON-IDENTITY entity "
        "types SecureBERT-NER extracted at >=0.999, into an averaged "
        "SGDClassifier. EMAIL, USERNAME and MACHINE are excluded and cost "
        "0.0000 ROC to remove",
        "the 326 ES notable keys present on >=1% of alerts, each as 'key value' "
        "text, then each >=0.999 entity as 'label value' across IPV4, IPV6, "
        "URL, FILEPATH, FILENAME and HASH (word 1-2gram, min_df=2, "
        "sublinear_tf); timestamps, epochs and bare years scrubbed; "
        "extract_artifacts and splunk_query dropped for Apollo alignment"),
}
STACK, TFIDF_NOTE = STACKS["text_v20"]

# each input column is built by a different module
DATASETS = {"text_v13": V13.OUT, "text_v18": NT.OUT, "text_v20": V20.OUT}

# The one vectoriser setting that differs between releases. Held here rather than
# on `sgd_model.WORD` so that re-cutting v11 cannot pick up v18's value.
MIN_DF = {"text_v13": 2, "text_v18": 1, "text_v20": 1}
SGD_ALPHA = 1e-5          # chosen by an out-of-fold sweep; see README
SGD_AVERAGE = True

HERE = Path(__file__).resolve().parent
MODELS = HERE / "models"

DATASET = NT.OUT
SOURCE = D12.SOURCE       # one definition of where the data came from
MISS_BUDGETS = (0, 1, 2, 5, 10, 20, 50)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_scores(score: np.ndarray) -> str:
    """Fingerprint of the model's output, rounded so it survives a float hair."""
    return hashlib.sha256(np.round(score, 9).tobytes()).hexdigest()


def git_sha() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE,
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except Exception:
        return None


def next_version() -> str:
    MODELS.mkdir(exist_ok=True)
    used = [int(p.name[1:]) for p in MODELS.glob("v*") if p.name[1:].isdigit()]
    return f"v{max(used, default=0) + 1}"


def evaluate_scores(y, score):
    thresholds = {}
    for allowed in MISS_BUDGETS:
        share, thr = MX.zero_miss_point(y, score, allowed)
        ai = score < thr if share else np.zeros(len(y), bool)
        thresholds[str(allowed)] = {
            "threshold": round(float(thr), 6),
            "ai_share_pct": round(float(share), 2),
            "ai_lane_rows": int(ai.sum()),
            "missed": int(y[ai].sum()),
            "residual_rate_pct": round(float(y[ai].mean() * 100) if ai.sum() else 0.0, 3),
        }
    q = pd.qcut(score, 5, labels=False, duplicates="drop")
    calibration = [
        {"fifth": int(i) + 1,
         "predicted_pct": round(float(pd.Series(score).groupby(q).mean().iloc[i] * 100), 1),
         "actual_pct": round(float(pd.Series(y).groupby(q).mean().iloc[i] * 100), 1)}
        for i in range(q.max() + 1)
    ]
    return score, {
        "roc_auc": round(float(roc_auc_score(y, score)), 4),
        "pr_auc": round(float(average_precision_score(y, score)), 4),
        "base_rate_pct": round(float(y.mean() * 100), 2),
        "test_rows": int(len(y)),
        "test_positives": int(y.sum()),
        "operating_points": thresholds,
        "calibration_by_score_fifth": calibration,
    }




def make(alpha=SGD_ALPHA):
    """(estimator factory, the columns it consumes).

    The column list is what the estimator actually reads, not everything the
    table happens to hold - a release that claims 36 features when its
    ColumnTransformer selects one is lying to whoever deploys it.
    """
    min_df = MIN_DF.get(SM.INPUT, 2)
    return (lambda rows: SM.build(alpha, SGD_AVERAGE, min_df).fit(
        SM.columns(rows), rows["y_human"]), [SM.INPUT])


def oof_stack(frame, fit_on, features, folds=5):
    """Expanding forward folds: every block scored by a model fitted only on
    what came before it."""
    frame = frame.sort_values("t").reset_index(drop=True)
    edges = np.linspace(0, len(frame), folds + 2, dtype=int)[1:]
    score = np.full(len(frame), np.nan)
    for lo, hi in zip(edges[:-1], edges[1:]):
        model = fit_on(frame.iloc[:lo])
        score[lo:hi] = model.predict_proba(frame.iloc[lo:hi][features])[:, 1]
    keep = ~np.isnan(score)
    return frame[keep], score[keep]


# Key lists are per release: the file is copied into the release folder so the
# frozen contract does not point at anything still editable.
KEY_LISTS = {"text_v20": ("apollo_keys.json", HERE / "config" / "apollo_keys.json")}

# One line per stack, so a release folder says what it was fitted on without
# anyone having to read the dataset module that built it.
FEATURE_NOTES = {
    "text_v13": ("the 326 ES notable keys present on >=1% of alerts, plus the six "
                 "non-identity entity types SecureBERT-NER extracted at >=0.999"),
    "text_v18": "the whole ES notable artifact, every field that survives the drop set",
    "text_v20": ("the 937 ES notable keys present in BOTH the training corpus and the "
                 "Apollo export - the intersection, so nothing is missing at inference"),
}

RELEASE_CONFIG_HEAD = """# =============================================================================
# {version} - THE FROZEN PREPROCESSING CONTRACT FOR THIS RELEASE
#
# Written by freeze.py. Self-contained on purpose: a frozen release is a
# snapshot, and its folder has to describe how to reproduce it without reference
# to anything that can still be edited. `../../config/preprocessing.yaml` is the
# WORKING copy the next retrain reads; this file is what {version} was built with.
#
# Serving resolves this file from MODEL_VERSION, so rolling back to another
# release picks up that release's rules, key list and threshold together.
#
# `rules_sha256` covers the rule blocks only. The operating point is excluded:
# moving a threshold retrains nothing and must not invalidate a fingerprint.
# =============================================================================
"""


def write_release_config(out: Path, version: str, column: str, min_df: int,
                         metrics: dict) -> Path:
    """Snapshot the preprocessing rules + this release's own feature contract.

    The threshold written here is the release's zero-miss operating point, which
    is measured across the whole training window and under-delivers on recent
    traffic. `calibrate.py --version <v> --write` replaces it with a
    Clopper-Pearson one; until then the file says plainly that it is not
    calibrated, so nothing reads it as a promise.
    """
    import shutil
    import yaml

    doc = {"release": version,
           "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           **{k: CFG.CONFIG[k] for k in CFG.RULES if k in CFG.CONFIG}}

    key_list = None
    if column in KEY_LISTS:
        name, src = KEY_LISTS[column]
        if src.exists():
            shutil.copy2(src, out / name)
            key_list = name
    doc["features"] = {"input_column": column, "tfidf_min_df": min_df,
                       "key_list": key_list,
                       "description": FEATURE_NOTES.get(column, "")}

    zero = metrics["operating_points"]["0"]
    doc["operating_threshold"] = {
        "value": zero["threshold"],
        "calibrated": False,
        "method": ("the release's own zero-miss operating point, from out-of-fold "
                   "scores across the whole training window - NOT calibrated on a "
                   "recent window"),
        "measured": {"on": "out-of-fold, 5 expanding forward folds",
                     "auto_closed_pct": zero["ai_share_pct"],
                     "misses": zero["missed"]},
        "warning": (f"a whole-window zero-miss point under-delivers on recent "
                    f"traffic. Run `calibrate.py --version {version} --write` "
                    f"before serving this release."),
    }

    path = out / "preprocessing.yaml"
    path.write_text(RELEASE_CONFIG_HEAD.format(version=version) + yaml.safe_dump(
        doc, sort_keys=False, default_flow_style=False, width=88, allow_unicode=True))
    return path


def freeze(full: bool = False, version: str | None = None) -> Path:
    frame = pd.read_parquet(DATASET)
    train, test = DS.time_split(frame)
    fit_on, features = make()

    if full:
        # thresholds come from out-of-fold scores, the estimator from every row
        scored, oof = oof_stack(frame, fit_on, features)
        _, metrics = evaluate_scores(scored["y_human"].to_numpy(), oof)
        model = fit_on(frame)
        score = model.predict_proba(test[features])[:, 1]   # for the fingerprint
    else:
        model = fit_on(train)
        score = model.predict_proba(test[features])[:, 1]
        _, metrics = evaluate_scores(test["y_human"].to_numpy(), score)

    version = version or next_version()
    out = MODELS / version
    out.mkdir(parents=True)
    joblib.dump({"model": model, "features": features,
                 "target": "y_human", "version": version}, out / "model.joblib")

    params = {"input_column": SM.INPUT,
              "tfidf": TFIDF_NOTE,
              "sgd_loss": "log_loss", "sgd_penalty": "l2",
              "tfidf_min_df": MIN_DF.get(SM.INPUT, 2),
              "sgd_alpha": SGD_ALPHA, "sgd_average": SGD_AVERAGE,
              "calibration": "none - log_loss is already near the diagonal",
              "reads_tabular_features": False}
    manifest = {
        "version": version,
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": git_sha(),
        "task": "route an alert to an AI analyst or a human analyst",
        "stack": STACK,
        "labels": {
            "0_ai_lane": ["Benign Positive"],
            "1_human_lane": ["True Positive", "False Positive"],
            "dropped": ["Other"],
        },
        "decision_rule": ("score = P(needs a human). Route to the AI lane when "
                          "score < threshold, otherwise to a human."),
        "release_kind": "deployment (all rows)" if full else "holdout (evaluated)",
        "data": {
            "dataset_file": DATASET.name,
            "source_file": SOURCE.name,
            "source_sha256": sha256_file(SOURCE),
            "rows_total": int(len(frame)),
            "rows_trained_on": int(len(frame) if full else len(train)),
            "trained_through": str((frame if full else train)["t"].max()),
            "metrics_from": ("out-of-fold scores, 5 expanding forward folds"
                             if full else
                             "a single held-out window, last 30% by time"),
            "rows_scored_for_metrics": int(metrics["test_rows"]),
        },
        "features": features,
        "preprocessing": {
            # the release's OWN frozen copy, not the editable working one
            "config_file": "preprocessing.yaml",
            "config_sha256": CFG.CONFIG_SHA,
            "keys_dropped": len(CFG.DROP),
            "drop_groups": {name: len(g["keys"])
                            for name, g in CFG.CONFIG["drop_keys"].items()},
            "artifact_selection": CFG.CONFIG["artifact_selection"]["require_present"],
        },
        "excluded_features": {
            "identity": {"columns": DS.IDENTITY,
                         "reason": "memorises the training window; ROC falls "
                                   "0.840 -> 0.806 when included"},
            "clock": {"columns": ["hour", "weekday", "date"],
                      "reason": "_time is arrival not decision; the session "
                                "effect is a staffing artifact"},
        },
        "estimator": type(model).__name__,
        "hyperparameters": {k: v for k, v in params.items() if v is not None},
        "metrics": metrics,
        "predictions_sha256": sha256_scores(score),
        "environment": {
            "python": platform.python_version(),
            "scikit_learn": sklearn.__version__,
            "pandas": pd.__version__,
            "numpy": np.__version__,
        },
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    write_release_config(out, version, column, min_df, metrics)

    ops = metrics["operating_points"]
    kind = ("trained on all "
            f"{len(frame):,} rows; thresholds from out-of-fold scores over five "
            "expanding forward folds" if full else
            f"trained on the first {len(train):,} rows and measured on the "
            f"{len(test):,} it never saw")
    (out / "README.md").write_text(f"""# Router {version}

Frozen {manifest['frozen_at']} - {manifest['release_kind']}, {kind}. Do not edit
anything in this directory; cut a new version instead.

```python
import joblib
b = joblib.load("models/{version}/model.joblib")
score = b["model"].predict_proba(X[b["features"]])[:, 1]   # P(needs a human)
ai_lane = score < THRESHOLD
```

`X` must be built by `dataset.py`, which is the only place the prior-only
feature rules are enforced.

## Labels

| Disposition | Lane | y |
| --- | --- | --- |
| Benign Positive | AI | 0 |
| True Positive | Human | 1 |
| False Positive | Human | 1 |

## Measured on {metrics['test_rows']:,} rows not seen in fitting ({metrics['test_positives']} human-lane)

ROC AUC {metrics['roc_auc']} · PR AUC {metrics['pr_auc']} · base rate {metrics['base_rate_pct']}%

| Miss budget | Threshold | AI takes | Missed | Residual |
| --- | ---: | ---: | ---: | ---: |
""" + "\n".join(
        f"| {k} | {v['threshold']:.6f} | {v['ai_share_pct']:.1f}% | {v['missed']} |"
        f" {v['residual_rate_pct']:.2f}% |" for k, v in ops.items()
    ) + f"""

Calibrated: predicted against actual by score fifth runs """
        + ", ".join(f"{c['predicted_pct']:.0f}/{c['actual_pct']:.0f}"
                    for c in metrics["calibration_by_score_fifth"])
        + """.

## Provenance

Verify this artifact is the one that was measured:

```bash
.venv/bin/python freeze.py --verify
```

It re-scores the held-out window and compares against `predictions_sha256` in
`manifest.json`.
""")
    return out


def verify() -> int:
    """Re-score every release and check it against its own fingerprints.

    Each release names the table it was cut from, so load that one rather than
    today's default - v9 reads the notable blob and v10 reads the rule fields,
    and neither table carries the other's column.
    """
    tables: dict[str, pd.DataFrame] = {}
    bad = 0
    for d in sorted(MODELS.glob("v*")):
        manifest = json.loads((d / "manifest.json").read_text())
        bundle = joblib.load(d / "model.joblib")
        name = manifest["data"]["dataset_file"]
        if name not in tables:
            path = HERE / name
            if not path.exists():
                print(f"{d.name}  SUPERSEDED - {name} is gone")
                continue
            tables[name] = pd.read_parquet(path)
        frame = tables[name]
        _, test = DS.time_split(frame)
        missing = [c for c in bundle["features"] if c not in test.columns]
        if missing or manifest["data"]["rows_total"] != len(frame):
            # the dataset definition moved on; this release measured a different
            # population and cannot be re-scored from the current table
            print(f"{d.name}  SUPERSEDED - built on {manifest['data']['rows_total']:,} "
                  f"rows against today's {len(frame):,}"
                  + (f", missing {missing}" if missing else ""))
            continue
        score = bundle["model"].predict_proba(test[bundle["features"]])[:, 1]
        ok = sha256_scores(score) == manifest["predictions_sha256"]
        csv_ok = sha256_file(SOURCE) == manifest["data"]["source_sha256"]
        bad += not (ok and csv_ok)
        # a full release was fitted on these rows, so re-scoring them proves the
        # artifact is intact but says nothing about accuracy - quote the manifest
        print(f"{d.name}  predictions {'match' if ok else 'DIFFER'}   "
              f"source {'match' if csv_ok else 'DIFFER'}   "
              f"roc {manifest['metrics']['roc_auc']} "
              f"({manifest['data'].get('metrics_from', 'held-out window')})")
    return bad


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true",
                    help="re-score every frozen release and check its fingerprints")
    ap.add_argument("--full", action="store_true",
                    help="train on every row; thresholds from out-of-fold scores")
    ap.add_argument("--version", default=None,
                    help="name the release explicitly instead of taking the next number")
    ap.add_argument("--input", default="text_v20", choices=sorted(STACKS),
                    help="the text column the release reads")
    args = ap.parse_args()
    # every consumer - the pipeline, the column selector, the manifest - reads
    # SM.INPUT at call time, so setting it here is the whole switch
    SM.INPUT = args.input
    STACK, TFIDF_NOTE = STACKS[args.input]
    if args.input in DATASETS:
        DATASET = DATASETS[args.input]
    if args.verify:
        sys.exit(1 if verify() else 0)
    path = freeze(full=args.full, version=args.version)
    print(f"frozen -> {path.relative_to(HERE)}")
    for line in (path / "README.md").read_text().splitlines():
        if line.startswith("| ") and "%" in line:
            print("  " + line)
