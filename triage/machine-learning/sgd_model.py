"""TF-IDF over the ES notable into an SGDClassifier - the shipping router.

The alert arrives as a Splunk ES notable artifact. `dataset12.py` flattens every
field of it to `key value` text; this vectorises that text as word 1-2 grams and
fits a linear model over it. One weight per term, no dimensionality reduction:
linear models are the natural fit for wide sparse text, and at 168,679 terms and
283 non-zeros per row this is very wide and very sparse.

`log_loss` is the loss, because the router needs a probability to threshold, not
a margin. No calibration wrapper - log_loss on TF-IDF already lands near the
diagonal, and wrapping it cost 0.03 ROC for nothing.

    .venv/bin/python sgd_model.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import SGDClassifier
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.pipeline import Pipeline

import dataset as DS
import metrics as MX

HERE = Path(__file__).resolve().parent
SEED = 7

# `min_df=1` keeps terms seen exactly once. The usual reason to drop them - a term
# seen once cannot generalise - is wrong for this data: a rule name or a host
# identifier that appears on a handful of alerts is precisely what lets the model
# be *certain* an alert is benign, and certainty at the low tail is the only thing
# the router uses.
#
# Measured over three seeds, out-of-fold, against min_df=2:
#
#     ROC              0.8523 -> 0.8541
#     PR               0.6887 -> 0.6900
#     auto-closed @ 0 misses   0.96% -> 1.50%
#     auto-closed @ 5 misses   1.96% -> 3.15%
#
# `binary=True` scores higher still on ROC (0.8568) and was rejected: it collapses
# zero-miss coverage to 0.12%, because binary weighting sharpens the middle of the
# distribution at the cost of the low tail.
WORD = dict(analyzer="word", ngram_range=(1, 2), min_df=2, sublinear_tf=True,
            strip_accents="unicode", lowercase=True)


def word(min_df: int = 2) -> dict:
    """The vectoriser settings for a release.

    `min_df` is the only one that differs between live releases, and it is passed
    explicitly rather than mutated on `WORD`, so re-cutting an old release cannot
    silently pick up a newer default.
    """
    return {**WORD, "min_df": min_df}

# the one column the shipped model reads
INPUT = "text_cef"

def sgd(alpha: float = 1e-5, average: bool = True) -> SGDClassifier:
    """`average=True` averages the weight vector over updates, which closes most
    of the gap to a batch solver on sparse text and is cheap at this size."""
    return SGDClassifier(
        loss="log_loss",          # a probability to threshold, not a margin
        penalty="l2",
        alpha=alpha,
        average=average,
        max_iter=10000 if average else 3000,
        tol=1e-6 if average else 1e-4,
        early_stopping=not average,
        validation_fraction=0.15,
        n_iter_no_change=20,
        random_state=SEED,
    )


def features(min_df: int = 2) -> ColumnTransformer:
    return ColumnTransformer([("cef_word", TfidfVectorizer(**word(min_df)), INPUT)],
                             remainder="drop")


def build(alpha: float = 1e-5, average: bool = True, min_df: int = 2) -> Pipeline:
    return Pipeline([("features", features(min_df)), ("sgd", sgd(alpha, average))])


def columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Exactly what the estimator consumes - nothing else travels with it."""
    return frame[[INPUT]]


def evaluate(name, model, train, test):
    model.fit(columns(train), train["y_human"])
    s = model.predict_proba(columns(test))[:, 1]
    y = test["y_human"].to_numpy()
    cov = [MX.zero_miss_point(y, s, b)[0] for b in (0, 5, 10, 20)]
    q = pd.qcut(s, 5, labels=False, duplicates="drop")
    cal = " ".join(f"{pd.Series(s).groupby(q).mean().iloc[i]*100:.0f}/"
                   f"{pd.Series(y).groupby(q).mean().iloc[i]*100:.0f}"
                   for i in range(q.max() + 1))
    print(f"  {name:22s} ROC {roc_auc_score(y, s):.4f}  PR {average_precision_score(y, s):.4f}"
          "   AI% @<=0/5/10/20: " + " ".join(f"{c:5.1f}" for c in cov)
          + f"   cal {cal}")
    return s


def main():
    frame = pd.read_parquet(HERE / "dataset12.parquet")
    train, test = DS.time_split(frame)
    print(f"train {len(train):,}  test {len(test):,}  "
          f"base rate {test.y_human.mean()*100:.1f}%\n")
    print("regularisation sweep")
    for alpha in (1e-6, 1e-5, 3e-5, 1e-4):
        evaluate(f"alpha={alpha:g}", build(alpha), train, test)


if __name__ == "__main__":
    main()
