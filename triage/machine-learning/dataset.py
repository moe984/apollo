"""Build the routing dataset from the 90-day container disposition extract.

    Human lane  (y = 1)  True Positive + False Positive   2,926   27.5%
    AI lane     (y = 0)  Benign Positive                  7,715   72.5%
    dropped              Other, and anything with no verdict

Two rules govern every feature in here:

  * **Prior-only.** A row's features are built from rows that closed strictly
    before it. Nothing about the row itself, nothing from its future. The table
    is therefore safe to split by time without leaking.
  * **No clock, and no `_time` in the features at all.** `_time` survives only to
    order the rows and to cut the forward split - without an ordering there is no
    "prior", so the leak-free guarantee dies with it. Nothing derived from it
    reaches the model: not the hour, not the weekday, not the date, and no longer
    the elapsed gap since the same alert last fired.

Columns deliberately absent: `soar_instance`, `container_status`, `sa_decision`
and every feature derived from `_time`.

This module owns the prior-only feature rules and nothing else. The table itself
is built by `dataset12.py`, which supplies the normalised extract.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RULE = "Final_Rule_Name"     # the rule dimension, after fallback to the search
HUMAN = ["True Positive", "False Positive"]
AI = ["Benign Positive"]
KEEP = HUMAN + AI


def verdicts(df: pd.DataFrame) -> pd.DataFrame:
    """Every container a human reached a verdict on, in the order it arrived.

    The population is defined by the verdict alone. An earlier cut also required
    `container_status == closed` and `sa_decision == close_with_disposition`,
    which quietly dropped 2,467 rows that were 70.2% human-lane - 1,682 true
    positives, more than the 1,004 it kept. Escalated alerts are the clearest
    evidence a human was needed, so excluding them was training the router on
    the easy half of its own question.
    """
    v = df[df["Final_Disposition"].isin(KEEP)]
    return v.dropna(subset=["t"]).sort_values("t").reset_index(drop=True)


def title_of(name: pd.Series) -> pd.Series:
    """The alert title a rule fired on: the tail of the container name."""
    return name.str.split(" - ").str[-1].str.strip().str.lower()


def _running(keys, hits, laplace_n=20.0, prior=None):
    """Prior-only smoothed rate per key, plus the prior count.

    The rate a key carries on row i uses only rows < i. Smoothing pulls a key
    with two observations towards the base rate instead of letting it claim 0%
    or 100% - the single biggest source of spurious confidence in this data.
    `laplace_n=0` gives the raw rate instead, NaN until a key is first seen.

    `prior` defaults to the population's own base rate. Hard-coding it is a trap:
    a stale prior drags every unseen key towards the wrong answer, and here it
    would drag them towards the AI lane - the unsafe direction, where something
    never seen before looks safer than it is.
    """
    prior = float(np.mean(hits)) if prior is None else prior
    n_seen, n_hit = {}, {}
    rate, count = np.empty(len(keys)), np.empty(len(keys))
    for i, (k, h) in enumerate(zip(keys, hits)):
        n = n_seen.get(k, 0)
        count[i] = n
        denom = n + laplace_n
        rate[i] = ((n_hit.get(k, 0) + laplace_n * prior) / denom
                   if denom > 0 else np.nan)
        n_seen[k] = n + 1
        n_hit[k] = n_hit.get(k, 0) + bool(h)
    return rate, count


def build(df: pd.DataFrame) -> pd.DataFrame:
    v = verdicts(df)

    y_human = v["Final_Disposition"].isin(HUMAN).to_numpy()
    y_tp = v["Final_Disposition"].eq("True Positive").to_numpy()

    rule = v[RULE].to_numpy()
    tenant = v["tenant_name"].to_numpy()
    pair = list(zip(tenant, rule))
    title = title_of(v["container_name"])
    rule_title = list(zip(rule, title.to_numpy()))

    f = pd.DataFrame(index=v.index)

    # --- what this customer has seen from this rule before -------------------
    seen, benign_run, last = {}, {}, {}
    f_seen = np.empty(len(v))
    f_run = np.empty(len(v))
    f_last = np.empty(len(v), dtype=object)
    for i, k in enumerate(pair):
        f_seen[i] = seen.get(k, 0)
        f_run[i] = benign_run.get(k, 0)
        f_last[i] = last.get(k, "none")
        seen[k] = seen.get(k, 0) + 1
        is_benign = v["Final_Disposition"].iat[i] == "Benign Positive"
        benign_run[k] = benign_run.get(k, 0) + 1 if is_benign else 0
        last[k] = v["Final_Disposition"].iat[i]
    f["pair_seen"] = f_seen
    f["pair_benign_run"] = f_run
    f["pair_last"] = pd.Categorical(f_last,
                                    categories=["none", "Benign Positive",
                                                "True Positive", "False Positive"])
    f["pair_human_rate"], _ = _running(pair, y_human)

    # --- the rule's own track record, across every customer ------------------
    f["rule_human_rate"], f["rule_n"] = _running(rule, y_human)
    f["rule_tp_rate"], _ = _running(rule, y_tp)
    # unsmoothed, for threshold policies that want the rate at face value
    f["rule_human_raw"], _ = _running(rule, y_human, laplace_n=0.0, prior=0.0)

    # how often the rule has changed its mind: of the firings that had a
    # previous verdict on the same customer, the share that differed from it
    flips, seen_pair, prev_of = {}, {}, {}
    f_flip = np.empty(len(v))
    f_flip_n = np.empty(len(v))
    for i, (r, k) in enumerate(zip(rule, pair)):
        n = seen_pair.get(r, 0)
        f_flip_n[i] = n
        f_flip[i] = flips.get(r, 0) / n if n else np.nan
        d = v["Final_Disposition"].iat[i]
        if k in prev_of:
            seen_pair[r] = n + 1
            if prev_of[k] != d:
                flips[r] = flips.get(r, 0) + 1
        prev_of[k] = d
    f["rule_flip_rate"] = f_flip
    f["rule_flip_n"] = f_flip_n

    # how widely the rule is deployed, as known so far
    tenants_of = {}
    f_breadth = np.empty(len(v))
    for i, (r, t) in enumerate(zip(rule, tenant)):
        s = tenants_of.setdefault(r, set())
        f_breadth[i] = len(s)
        s.add(t)
    f["rule_tenants"] = f_breadth

    # --- the customer's own history -----------------------------------------
    f["tenant_human_rate"], f["tenant_n"] = _running(tenant, y_human)

    # --- the alert title, which carries signal the rule name does not --------
    f["title_human_rate"], f["title_n"] = _running(rule_title, y_human)

    # --- properties of the container itself, known on arrival ---------------
    f["name_len"] = v["container_name"].str.len()
    f["name_parts"] = v["container_name"].str.count(" - ") + 1

    # --- identity, for a model that can use it; drop for the portable one ----
    f["rule"] = pd.Categorical(rule)
    f["tenant"] = pd.Categorical(tenant)

    # --- raw text, for the vectoriser to fit on inside the pipeline ----------
    # kept out of `feature_names` so it can never reach the estimator unvectorised
    f["text_name"] = v["container_name"].to_numpy()
    f["text_rule"] = rule
    f["text_title"] = title.to_numpy()
    # the raw detection identifiers, kept unnormalised: `rule_name` is what the
    # detection is called, `search_name` the saved search behind it. Both are
    # pure identity, which TF-IDF turns into shared sub-tokens across families
    # ("Defender", "Brute Force") rather than 313 unrelated labels.
    f["text_rule_raw"] = v["rule_name"].astype(str).to_numpy()
    f["text_search"] = v["search_name"].astype(str).to_numpy()

    f["y_human"] = y_human.astype(int)
    f["y_tp"] = y_tp.astype(int)
    f["t"] = v["t"].to_numpy()
    f["disposition"] = v["Final_Disposition"].to_numpy()
    f["container_id"] = v["container_id"].to_numpy()
    return f


IDENTITY = ["rule", "tenant"]
TEXT = ["text_name", "text_rule", "text_title", "text_rule_raw", "text_search",
        "text_cef"]
META = ["y_human", "y_tp", "t", "disposition", "container_id"]


def text_names(frame: pd.DataFrame) -> list[str]:
    """The TEXT columns this frame actually has.

    The two populations do not carry the same text: `text_desc` comes from the
    ES notable artifact and exists only in the 12-month table. Callers that build
    a column list for a pipeline must ask the frame, not the constant, or the
    90-day releases stop being re-scorable.
    """
    return [c for c in TEXT if c in frame.columns]


def feature_names(frame: pd.DataFrame, identity: bool = True,
                  text: bool = False) -> list[str]:
    """The tabular feature list. Text columns are opt-in: they are raw strings
    and only a fitted vectoriser may turn them into numbers."""
    drop = set(META) | (set() if text else set(TEXT)) | (set() if identity else set(IDENTITY))
    return [c for c in frame.columns if c not in drop]


def time_split(frame: pd.DataFrame, holdout: float = 0.30):
    """Train on what came first, test on what came after - the way it deploys."""
    cut = frame["t"].quantile(1 - holdout)
    return frame[frame["t"] <= cut].copy(), frame[frame["t"] > cut].copy()
