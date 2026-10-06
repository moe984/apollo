"""Load and prepare the 90-day container disposition extract.

Normalisations applied (kept explicit so the EDA never hides them):
  * `_time` literal "None" -> NaT (12 rows); those rows drop out of date filters.
  * rule_name "" / "None" -> "(unattributed)" (1,528 rows, all KSU/SOAR1 bar 14).
  * Final_Rule_Name is the backfilled rule carried in the CSV: rule_name where it
    exists, otherwise recovered from search_name (1,130 rows via the rule_name that
    other rows carry for the same search, 384 by stripping the category prefix and
    " - Rule" suffix). Only 14 rows stay unattributed. The app charts THIS column;
    swap RULE_COL back to "rule_name" to see the raw field instead.
  * closure_disposition typo variant "...Incorrect analyatical logic" folded into
    "False Positive - Incorrect Analytic Logic"; "" / "None" -> "(none)".
  * Final_Disposition is the rolled-up verdict carried in the CSV: True Positive
    (true positive + undetermined), False Positive (every false-positive variant),
    Benign Positive, Other. Blank where no disposition was recorded; "" -> "(none)".
  * recurrence gap is computed on the FULL non-duplicate set before filtering, so
    a prior occurrence still counts when the filter hides it.
"""
from pathlib import Path

import pandas as pd

RULE_COL = "Final_Rule_Name"     # the rule dimension every chart groups by

CSV = Path(__file__).resolve().parent.parent / "mom_container_disposition_12mo.csv"

DISPOSITION_FIX = {
    "False Positive - Incorrect analyatical logic": "False Positive - Incorrect Analytic Logic",
}
RECUR_BUCKETS = ["within 1h", "1h - 24h", "24h - 7d", "over 7d", "first occurrence"]


def _bucket(gap):
    if pd.isna(gap):
        return "first occurrence"
    h = gap / pd.Timedelta("1h")
    if h <= 1:
        return "within 1h"
    if h <= 24:
        return "1h - 24h"
    if h <= 24 * 7:
        return "24h - 7d"
    return "over 7d"


def load(path=CSV):
    df = pd.read_csv(path, dtype=str, keep_default_na=False)

    df["t"] = pd.to_datetime(df["_time"].where(df["_time"] != "None"),
                             format="ISO8601", utc=True, errors="coerce")
    df["day"] = df["t"].dt.tz_convert("UTC").dt.floor("D")

    df["rule_name"] = (df["rule_name"].replace({"": "(unattributed)", "None": "(unattributed)"}))
    df["Final_Rule_Name"] = df["Final_Rule_Name"].replace({"": "(unattributed)",
                                                           "None": "(unattributed)"})
    df["tenant_name"] = df["tenant_name"].replace({"": "(unknown)", "None": "(unknown)"})
    df["closure_disposition"] = (df["closure_disposition"]
                                 .replace(DISPOSITION_FIX)
                                 .replace({"": "(none)", "None": "(none)"}))
    df["sa_decision"] = df["sa_decision"].replace({"": "(none)"})
    df["Final_Disposition"] = df["Final_Disposition"].replace({"": "(none)", "None": "(none)"})

    df["is_dup"] = df["container_status"].eq("duplicate")
    df["is_esc"] = df["sa_decision"].eq("escalate")
    df["is_closed"] = df["container_status"].eq("closed")

    # Residual recurrence: among containers NOT already deduplicated, how long
    # since the last identical tenant + container_name.
    nd = df[~df["is_dup"]].sort_values("t")
    gap = nd.groupby(["tenant_name", "container_name"], sort=False)["t"].diff()
    df["recur_bucket"] = pd.Series(pd.NA, index=df.index, dtype="object")
    df.loc[nd.index, "recur_bucket"] = gap.map(_bucket)
    df.loc[nd["t"].isna().reindex(df.index, fill_value=False), "recur_bucket"] = pd.NA

    return df


def options(df):
    return {
        "instances": sorted(df["soar_instance"].unique()),
        "tenants": sorted(df["tenant_name"].unique()),
        "rules": df[RULE_COL].value_counts().index.tolist(),
        "tmin": df["t"].min(),
        "tmax": df["t"].max(),
    }


def apply_filters(df, start, end, instances, tenants, rules):
    m = pd.Series(True, index=df.index)
    if start:
        m &= df["t"] >= pd.Timestamp(start, tz="UTC")
    if end:
        m &= df["t"] < pd.Timestamp(end, tz="UTC") + pd.Timedelta("1D")
    if instances:
        m &= df["soar_instance"].isin(instances)
    if tenants:
        m &= df["tenant_name"].isin(tenants)
    if rules:
        m &= df[RULE_COL].isin(rules)
    return df[m]
