"""Turn an Apollo record into the exact string the model was fitted on.

This is a **vendored copy** of the training-time rules, so the service deploys
without the training repo on the path. `tests/test_parity.py` asserts it produces
byte-identical output to `machine-learning/dataset_v11.to_text` on real alerts -
if the two ever drift, that test fails rather than the model quietly scoring a
different string than it was trained on.

Source of truth: `machine-learning/dataset12.py` and `machine-learning/dataset_v11.py`.
"""
from __future__ import annotations

import hashlib
import json
import os
import re

import yaml
from pathlib import Path

# The preprocessing contract is resolved FROM THE RELEASE, not from a shared
# file. Each frozen release carries its own `preprocessing.yaml` holding the drop
# rules, its feature definition (min_df, key list) and its own operating
# threshold - so rolling back with MODEL_VERSION picks all three up together.
#
# Before this split there was one shared config, and serving v18 would silently
# have applied v20's 937-key filter and v20's threshold. `tests/test_parity.py`
# asserts each release's copy is byte-identical to the training-side original.
HERE = Path(__file__).resolve().parent.parent
MODELS = Path(os.environ.get("MODEL_DIR", HERE / "models"))
VERSION = os.environ.get("MODEL_VERSION", "v20")

CONFIG_PATH = MODELS / VERSION / "preprocessing.yaml"
if not CONFIG_PATH.exists():
    raise FileNotFoundError(
        f"{CONFIG_PATH} is missing - release {VERSION} has no frozen preprocessing "
        f"contract, so the service cannot know which keys or threshold it was built "
        f"with. Re-cut the release with freeze.py, which writes this file.")
CONFIG = yaml.safe_load(CONFIG_PATH.read_text())

# Only these sections change what the model is fitted on. `operating_threshold`
# lives in the same file but is excluded: recalibrating must not invalidate a
# frozen release, because it retrains nothing.
RULES = ("artifact_selection", "drop_keys", "empty_values",
         "scrub_time_values", "rendering")
CONFIG_SHA = hashlib.sha256(json.dumps(
    {k: CONFIG[k] for k in RULES if k in CONFIG},
    sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

# The configured operating point. MISS_BUDGET / THRESHOLD env vars still win.
CONFIGURED_THRESHOLD: float | None = (
    CONFIG.get("operating_threshold", {}).get("value"))
THRESHOLD_NOTE: dict = CONFIG.get("operating_threshold", {})

# Some releases restrict the input to an allow-list of keys; some do not. v20 is
# fitted only on fields Apollo has actually been observed to forward, so the
# service applies the same restriction - a field outside the list contributes
# nothing at inference anyway, but excluding it makes the served string identical
# to the trained one rather than merely equivalent. v18 and v13 use no list, and
# for them APOLLO_KEYS is None, which `to_text` reads as "keep everything".
#
# The release names its own list, so this is never v20's list applied to v18.
FEATURES: dict = CONFIG.get("features") or {}
_KEY_LIST: str | None = FEATURES.get("key_list")
KEYS_PATH = (CONFIG_PATH.parent / _KEY_LIST) if _KEY_LIST else None
if KEYS_PATH is not None and not KEYS_PATH.exists():
    raise FileNotFoundError(
        f"{CONFIG_PATH.name} names key_list={_KEY_LIST!r} but {KEYS_PATH} is missing - "
        f"release {VERSION} would score a different string than it was fitted on.")
_KEYS = json.loads(KEYS_PATH.read_text()) if KEYS_PATH else None
APOLLO_KEYS: set[str] | None = set(_KEYS["keys"]) if _KEYS else None
KEYS_SHA = (hashlib.sha256(KEYS_PATH.read_bytes()).hexdigest()
            if KEYS_PATH else None)

# --------------------------------------------------------------------------
# which artifact is the alert
# --------------------------------------------------------------------------

def artifacts(arr):
    """A container's CEF artifacts as dicts (objects or JSON strings)."""
    for item in (arr if arr is not None else ()):
        if isinstance(item, dict):
            yield item
            continue
        try:
            obj = json.loads(item)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            yield obj


def is_notable(obj: dict) -> bool:
    """True for the Splunk ES notable that opened the container.

    A SOAR container carries up to four families of artifact and only this one
    predates the analyst:

      * the ES notable          - `rule_id` present, no `soar_event`
      * a settings/metrics blob - carries `SA_DISPOSITION`, which IS the label
      * `soar_event=*`          - written while the container is worked
      * a Slack/escalation note - only exists once escalated to a human

    Absence of `soar_event` is NOT the test: two of the four families lack it.
    `rule_id` present and `SA_DISPOSITION` absent is.

    Apollo forwards only the notable, so in practice `soar_cef[0]` always passes.
    This is checked anyway - a payload change upstream should fail loudly here
    rather than silently feed the model a triage artifact.
    """
    return (all(k in obj for k in REQUIRE_PRESENT)
            and not any(k in obj for k in REQUIRE_ABSENT))


def notable_of(record: dict) -> dict | None:
    """The ES notable Apollo forwarded for this alert, or None."""
    for obj in artifacts(record.get("soar_cef")):
        if is_notable(obj):
            return obj
    return None


# --------------------------------------------------------------------------
# which fields, and which values
# --------------------------------------------------------------------------

# Splunk plumbing, per-event identifiers, ES-side triage state, and the two keys
# that exist on only one side of the SOAR/Apollo boundary.
#
# A bucket id or a search id is unique to one alert, so it can only ever be a
# memorised handle on a single training row. `status` and `owner` describe triage
# state, which moves *after* the alert arrives - reading them would leak.
DROP: set[str] = {k for g in CONFIG["drop_keys"].values() for k in g["keys"]}
NOT_A_VALUE: tuple[str, ...] = tuple(CONFIG["empty_values"]["values"])
TIME_TOKEN = re.compile(CONFIG["scrub_time_values"]["pattern"].strip(), re.I)

REQUIRE_PRESENT = tuple(CONFIG["artifact_selection"]["require_present"])
REQUIRE_ABSENT = tuple(CONFIG["artifact_selection"]["require_absent"])


def why_dropped(key: str) -> str | None:
    """Which group dropped this key, with the configured reason."""
    for name, group in CONFIG["drop_keys"].items():
        if key in group["keys"]:
            return f"{name}: {group['reason']}"
    return None


def scrub_time(text: str) -> str:
    return TIME_TOKEN.sub(" ", text)


def flatten(value) -> str:
    """A CEF value as one space-joined string."""
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    return "" if value is None else str(value)


def to_text(notable: dict | None, keep: set[str] | None = None) -> str:
    """The notable as `key value` pairs - the exact input the model reads.

    Pairs rather than bare values so a word bigram can bind a field to its
    reading: `severity low` and `urgency low` stay distinguishable terms.
    """
    if not notable:
        return ""
    if keep is None:
        keep = APOLLO_KEYS
    parts = []
    for key in sorted(notable):
        if key in DROP or (keep is not None and key not in keep):
            continue
        value = scrub_time(flatten(notable[key])).strip()
        if value and value.lower() not in NOT_A_VALUE:
            parts.append(f"{key} {value}")
    return " ".join(parts).lower()


def explain(notable: dict) -> dict:
    """Per-key account of what happened - for the /explain endpoint."""
    kept, dropped = {}, {}
    for key in sorted(notable):
        raw = flatten(notable[key])
        if key in DROP:
            dropped[key] = f"dropped - {why_dropped(key)}"
            continue
        if APOLLO_KEYS is not None and key not in APOLLO_KEYS:
            dropped[key] = ("dropped - not in the trained key list; the model has "
                            "no weights for it (new field - retrain to use it)")
            continue
        scrubbed = scrub_time(raw).strip()
        if not scrubbed:
            dropped[key] = "dropped - value was entirely a timestamp"
        elif scrubbed.lower() in NOT_A_VALUE:
            dropped[key] = f"dropped - value is {scrubbed!r}"
        else:
            kept[key] = f"{key} {scrubbed}".lower()
    return {"kept": kept, "dropped": dropped,
            "n_kept": len(kept), "n_dropped": len(dropped)}
