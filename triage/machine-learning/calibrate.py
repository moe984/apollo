"""Recalibrate the operating threshold on recent traffic.

A threshold is a perishable artifact. The one in a manifest comes from
out-of-fold scores across the whole training window, and the alert population
moves - the not-benign rate ran 6.5% -> 25.3% over this year - so it stops
meaning what it says.

**The model is not retrained.** Only the line through its scores moves.

How the threshold is chosen
---------------------------
Score a window of labelled alerts the model has never seen, sort by score
lowest-first, and walk up the list. The naive answer is "cut where the first
not-benign alert appears" - but that is a trap:

    observing 0 mistakes in an 85-alert lane bounds the true error rate at
    3.5%, not at 0. The rule of three: 0 failures in n trials puts the 95%
    upper bound at roughly 3/n.

That is exactly why a zero-miss threshold chosen on one window let 6 alerts
through on the next, and failed on three of six rolling windows.

So instead of the observed error rate, this uses the **Clopper-Pearson exact
upper bound** on it, and picks the widest threshold whose bound stays under a
tolerance you state:

    tolerance 0.1%  ->  the AI lane's true error rate is, with 95% confidence,
                        under one in a thousand

The tolerance is a risk appetite, declared. It replaces an arbitrary safety
factor with a number that can be defended to a customer, and it predicts its own
failure rate honestly - at 1% tolerance the next window came back at 1.2%.

    .venv/bin/python calibrate.py                      # 8-week windows, 0.1%
    .venv/bin/python calibrate.py --tolerance 0.01     # trade safety for volume
    .venv/bin/python calibrate.py --weeks 6 --write    # update the config
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import beta

import metrics as MX
import sgd_model as SM

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config" / "preprocessing.yaml"
CONFIDENCE = 0.95
TOLERANCE = 0.001          # 0.1% - the shipped risk appetite


def fit(rows, column, min_df):
    from sklearn.compose import ColumnTransformer
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.pipeline import Pipeline
    return Pipeline([
        ("features", ColumnTransformer(
            [("cef_word", TfidfVectorizer(**SM.word(min_df)), column)],
            remainder="drop")),
        ("sgd", SM.sgd(1e-5, True)),
    ]).fit(rows[[column]], rows["y_human"])


def upper_bound(misses: int, lane: int, confidence: float = CONFIDENCE) -> float:
    """Clopper-Pearson exact upper bound on the AI lane's error rate.

    Exact rather than normal-approximate, because the counts are tiny and
    frequently zero - which is precisely where a normal approximation is worst.
    """
    if lane == 0:
        return 1.0
    if misses >= lane:
        return 1.0
    return float(beta.ppf(confidence, misses + 1, lane - misses))


def choose(y: np.ndarray, score: np.ndarray, tolerance: float = TOLERANCE,
           confidence: float = CONFIDENCE) -> dict:
    """The widest threshold whose error-rate upper bound stays under `tolerance`.

    Sort by score, walk up, and keep the largest cut that still satisfies the
    bound. Nothing here is a fudge factor: the safety margin falls out of how
    much evidence the window actually provides.
    """
    order = np.argsort(score, kind="mergesort")
    sorted_y, sorted_s = y[order], score[order]
    cumulative = np.cumsum(sorted_y)

    best = {"lane": 0, "threshold": float(sorted_s[0]), "misses": 0,
            "bound_pct": 0.0}
    for n in range(1, len(sorted_y) + 1):
        misses = int(cumulative[n - 1])
        bound = upper_bound(misses, n, confidence)
        if bound <= tolerance:
            best = {"lane": n, "threshold": float(sorted_s[n - 1]),
                    "misses": misses, "bound_pct": round(bound * 100, 4)}
    best["share_pct"] = round(best["lane"] / len(y) * 100, 2)
    return best


def calibrate(version: str = "v20", weeks: int = 8,
              tolerance: float = TOLERANCE) -> tuple[dict, pd.DataFrame]:
    import freeze as FZ
    column = f"text_{version}"
    frame = pd.read_parquet(FZ.DATASETS[column]).sort_values("t").reset_index(drop=True)
    min_df = FZ.MIN_DF[column]

    end = frame["t"].max()
    cal_from, val_from = end - pd.Timedelta(weeks=2 * weeks), end - pd.Timedelta(weeks=weeks)
    train = frame[frame["t"] < cal_from]
    cal = frame[(frame["t"] >= cal_from) & (frame["t"] < val_from)]
    val = frame[frame["t"] >= val_from]

    print(f"release {version}   column {column}   min_df {min_df}   "
          f"confidence {CONFIDENCE:.0%}\n")
    for name, part in (("train", train), ("calibrate", cal), ("validate", val)):
        print(f"  {name:10s} {len(part):6,}  {part.t.min():%Y-%m-%d} -> "
              f"{part.t.max():%Y-%m-%d}   {part.y_human.mean()*100:5.1f}% not benign")

    model = fit(train, column, min_df)
    s_cal, y_cal = model.predict_proba(cal[[column]])[:, 1], cal.y_human.to_numpy()
    s_val, y_val = model.predict_proba(val[[column]])[:, 1], val.y_human.to_numpy()

    rows = []
    for tol in (0.0005, 0.001, 0.002, 0.005, 0.01, 0.02):
        pick = choose(y_cal, s_cal, tol)
        lane = s_val < pick["threshold"]
        rows.append({
            "tolerance_pct": tol * 100,
            "threshold": round(pick["threshold"], 6),
            "chosen_pct": pick["share_pct"],
            "actual_pct": round(float(lane.mean() * 100), 2),
            "actual_misses": int(y_val[lane].sum()),
            "actual_residual_pct": round(float(y_val[lane].mean() * 100)
                                         if lane.sum() else 0.0, 3),
        })
    table = pd.DataFrame(rows)

    shipped = choose(y_cal, s_cal, tolerance)
    lane = s_val < shipped["threshold"]
    shipped |= {
        "tolerance": tolerance, "confidence": CONFIDENCE,
        "validated_share_pct": round(float(lane.mean() * 100), 2),
        "validated_misses": int(y_val[lane].sum()),
        "windows": {
            "train_through": f"{train.t.max():%Y-%m-%d}",
            "calibrate": f"{cal.t.min():%Y-%m-%d} .. {cal.t.max():%Y-%m-%d}",
            "validate": f"{val.t.min():%Y-%m-%d} .. {val.t.max():%Y-%m-%d}",
        },
    }

    naive = MX.zero_miss_point(y_cal, s_cal, 0)[1]
    nl = s_val < naive
    print(f"\n  the naive zero-miss point {naive:.6f} would auto-close "
          f"{nl.mean()*100:.2f}% and let {int(y_val[nl].sum())} through")
    return shipped, table


def release_config(version: str) -> Path:
    return HERE / "models" / version / "preprocessing.yaml"


THRESHOLD_NOTE = """
# ---------------------------------------------------------------------------
# THE OPERATING POINT - deliberately NOT part of rules_sha256
#
# The line through this release's scores. Below it an alert is called benign and
# the AI closes it. Changing this retrains nothing, which is exactly why it is
# excluded from the fingerprint.
#
# Chosen by the Clopper-Pearson exact upper bound on the AI lane's error rate:
# the widest threshold whose 95% upper bound stays under `tolerance`. Observing
# zero mistakes in a small lane bounds the true rate at 3/n, not at 0 - which is
# why a naive zero-miss threshold failed on three of six rolling windows.
#
# Recalibrate monthly:  .venv/bin/python calibrate.py --version {version} --write
# ---------------------------------------------------------------------------
"""


def write_config(pick: dict, version: str = "v20", path: Path | None = None) -> Path:
    """Replace `operating_threshold` in THIS RELEASE's config, rules untouched.

    Writes to `models/<version>/preprocessing.yaml`, not to the working copy:
    a threshold belongs to the release it was measured on, and calibrating v18
    must not move v20's line.
    """
    import yaml

    path = path or release_config(version)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} missing - re-cut {version} with freeze.py, which writes it.")
    text = path.read_text()
    config = yaml.safe_load(text)

    config["operating_threshold"] = {
        "value": round(pick["threshold"], 6),
        "calibrated": True,
        "calibrated_at": pd.Timestamp.utcnow().strftime("%Y-%m-%d"),
        "method": ("Clopper-Pearson exact upper bound on the AI-lane error rate, "
                   f"{pick['confidence']:.0%} confidence"),
        "tolerance": pick["tolerance"],
        "calibrated_on": pick["windows"],
        "measured": {
            "on_window": pick["windows"]["validate"],
            "auto_closed_pct": pick["validated_share_pct"],
            "misses": pick["validated_misses"],
        },
    }

    # keep the file's leading comment banner; re-dump everything below it
    lines = text.splitlines(keepends=True)
    head = "".join(lines[:next(i for i, ln in enumerate(lines)
                               if ln.strip() and not ln.lstrip().startswith("#"))])
    body = {k: v for k, v in config.items() if k != "operating_threshold"}
    path.write_text(
        head
        + yaml.safe_dump(body, sort_keys=False, default_flow_style=False,
                         width=88, allow_unicode=True)
        + THRESHOLD_NOTE.format(version=version)
        + yaml.safe_dump({"operating_threshold": config["operating_threshold"]},
                         sort_keys=False, default_flow_style=False, width=88))
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v20")
    ap.add_argument("--weeks", type=int, default=8)
    ap.add_argument("--tolerance", type=float, default=TOLERANCE,
                    help="acceptable AI-lane error rate, e.g. 0.001 for 0.1%%")
    ap.add_argument("--write", action="store_true",
                    help="update operating_threshold in models/<version>/preprocessing.yaml")
    a = ap.parse_args()

    pick, table = calibrate(a.version, a.weeks, a.tolerance)
    print("\nthreshold by stated tolerance - chosen on `calibrate`, "
          "measured on `validate`:\n")
    print(table.to_string(index=False))
    print(f"\n  TOLERANCE {a.tolerance*100:.2f}%  ->  THRESHOLD={pick['threshold']:.6f}")
    print(f"  on the validate window: {pick['validated_share_pct']:.2f}% auto-closed, "
          f"{pick['validated_misses']} misses")
    if a.write:
        written = write_config(pick, a.version)
        print(f"\n  wrote operating_threshold to {written.relative_to(HERE)}")
        print(f"  copy it to model-serving/models/{a.version}/ to deploy "
              f"(tests/test_parity.py asserts the two match)")
