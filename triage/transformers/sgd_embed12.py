"""ATTACK-BERT embeddings into an SGDClassifier.

The pairing worth testing: a tree ensemble handles 768 dense correlated
dimensions badly and needs them squeezed through SVD first, but a linear model
takes one weight per dimension and can read the whole vector untouched.

The vectors are L2-normalised by the encoder, so no scaling is needed. Evaluated
on the same forward split as every other model here, and against the TF-IDF
router (`ml/v9`, ROC 0.8523) on the same rows.

    .venv/bin/python sgd_embed12.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import (accuracy_score, average_precision_score, f1_score,
                             precision_score, recall_score, roc_auc_score)
from sklearn.model_selection import TimeSeriesSplit

HERE = Path(__file__).resolve().parent
ML = HERE.parent / "machine-learning"
sys.path.insert(0, str(ML))
import metrics as MX  # noqa: E402

SEED = 7


def sgd(alpha):
    return SGDClassifier(loss="log_loss", penalty="l2", alpha=alpha, average=True,
                         max_iter=10000, tol=1e-6, early_stopping=False,
                         n_iter_no_change=20, random_state=SEED)


def fit_score(X, y, n, alpha, calibrate):
    est = sgd(alpha)
    model = (CalibratedClassifierCV(est, method="sigmoid", cv=TimeSeriesSplit(4))
             if calibrate else est)
    model.fit(X[:n], y[:n])
    return model.predict_proba(X[n:])[:, 1]


def report(tag, y, s):
    cov = [MX.coverage(y, s, int(round(r / 100 * y.sum()))) for r in (0.5, 1, 2, 5)]
    p = (s >= 0.5).astype(int)
    print(f"  {tag:34s} ROC {roc_auc_score(y, s):.4f}  PR {average_precision_score(y, s):.4f}  "
          f"acc {accuracy_score(y, p)*100:5.2f}%  F1 {f1_score(y, p)*100:5.2f}%  "
          "| AI% @miss 0.5/1/2/5: " + " ".join(f"{c:5.1f}" for c in cov), flush=True)
    return roc_auc_score(y, s)


def main():
    frame = pd.read_parquet(ML / "dataset12.parquet").sort_values("t").reset_index(drop=True)
    n = int((frame["t"] <= frame["t"].quantile(0.70)).sum())
    y = frame["y_human"].to_numpy()
    yte = y[n:]
    files = sorted((HERE / "embeddings").glob("*__*__*__*.npy"))
    if not files:
        sys.exit("no embeddings built for the 12-month table - run embed12.py first")
    print(f"train {n:,}  test {len(frame)-n:,}  base rate {yte.mean()*100:.1f}%")
    print("reference: ml/v9 TF-IDF router, ROC 0.8523 out-of-fold\n")

    for path in files:
        X = np.load(path)
        if len(X) != len(frame):
            print(f"  {path.name}: {len(X):,} rows, table has {len(frame):,} - skipped")
            continue
        print(path.stem)
        for alpha in (1e-6, 1e-5, 1e-4, 1e-3):
            report(f"alpha={alpha:g}", yte, fit_score(X, y, n, alpha, False))
        report("alpha=1e-4, calibrated", yte, fit_score(X, y, n, 1e-4, True))
        print()


if __name__ == "__main__":
    main()
