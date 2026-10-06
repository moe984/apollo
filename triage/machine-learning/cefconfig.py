"""Load the preprocessing contract - the definition of what is dropped.

There are two kinds of config, and the difference matters:

  `config/preprocessing.yaml`     the WORKING copy. Editable, and what the next
                                  retrain reads. This module's globals come from
                                  it, so training code needs no changes.

  `models/<release>/preprocessing.yaml`
                                  a FROZEN snapshot, written by `freeze.py` when
                                  a release is cut. Self-contained: the rules,
                                  that release's feature definition (min_df, key
                                  list) and its own operating threshold.

A frozen release must not depend on a file that can still be edited, and rolling
back to an older release has to pick up that release's rules, key list and
threshold *together* - which is why serving resolves its config from the release
directory rather than from the working copy.

    from cefconfig import DROP, EMPTY, TIME_TOKEN, CONFIG_SHA   # working copy
    cfg = cefconfig.for_release("v18")                          # frozen snapshot
"""
from __future__ import annotations

import hashlib
import json
import re

import yaml
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATH = HERE / "config" / "preprocessing.yaml"


def load(path: Path = PATH) -> dict:
    return yaml.safe_load(path.read_text())


# Only these sections change what the model is fitted on. The threshold lives in
# the same file for convenience but is deliberately excluded: recalibrating must
# not invalidate a release, because it does not retrain anything.
RULES = ("artifact_selection", "drop_keys", "empty_values",
         "scrub_time_values", "rendering")


def rules_sha256(config: dict | None = None) -> str:
    """Fingerprint of the preprocessing RULES only, so a release can name them.

    Hashing the whole file would mean a threshold change looked like a training
    change, and every frozen release would fail its config check on the next
    recalibration.
    """
    config = config if config is not None else load()
    # hashed as canonical JSON, so the fingerprint survives YAML reformatting
    return hashlib.sha256(json.dumps(
        {k: config[k] for k in RULES if k in config},
        sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def sha256(path: Path = PATH) -> str:
    """Fingerprint of the whole file, including the threshold."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


MODELS = HERE / "models"


def release_path(version: str, models: Path | None = None) -> Path:
    return (models or MODELS) / version / "preprocessing.yaml"


def for_release(version: str, models: Path | None = None) -> dict:
    """The frozen contract a release was built with.

    Raises rather than silently falling back to the working copy: serving the
    wrong key list or the wrong threshold is the failure this split exists to
    prevent, and a missing file should stop a deploy, not degrade it.
    """
    path = release_path(version, models)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing - {version} has no frozen preprocessing contract. "
            f"Re-cut it with freeze.py, which writes this file alongside the model.")
    return load(path)


def keys_for_release(version: str, models: Path | None = None) -> set[str] | None:
    """The key allow-list a release restricts its input to, or None for all keys."""
    cfg = for_release(version, models)
    name = (cfg.get("features") or {}).get("key_list")
    if not name:
        return None
    path = release_path(version, models).parent / name
    return set(json.loads(path.read_text())["keys"])


CONFIG = load()
CONFIG_SHA = rules_sha256(CONFIG)     # the rules; what a release pins
FILE_SHA = sha256()                   # the whole file, threshold included

THRESHOLD: float = CONFIG["operating_threshold"]["value"]

# every key of every group, flattened
DROP: set[str] = {k for group in CONFIG["drop_keys"].values() for k in group["keys"]}
EMPTY: tuple[str, ...] = tuple(CONFIG["empty_values"]["values"])
TIME_TOKEN = re.compile(CONFIG["scrub_time_values"]["pattern"].strip(), re.I)

REQUIRE_PRESENT: tuple[str, ...] = tuple(CONFIG["artifact_selection"]["require_present"])
REQUIRE_ABSENT: tuple[str, ...] = tuple(CONFIG["artifact_selection"]["require_absent"])


def why(key: str) -> str | None:
    """Which group dropped this key, and the reason - for /predict responses."""
    for name, group in CONFIG["drop_keys"].items():
        if key in group["keys"]:
            return f"{name}: {group['reason']}"
    return None


if __name__ == "__main__":
    print(f"{PATH.relative_to(HERE)}")
    print(f"  rules sha256 {CONFIG_SHA[:16]}...  (pinned by each release)")
    print(f"  file  sha256 {FILE_SHA[:16]}...")
    print(f"  threshold    {THRESHOLD}  "
          f"({CONFIG['operating_threshold']['measured']['auto_closed_pct']}% auto-closed, "
          f"{CONFIG['operating_threshold']['measured']['misses']} misses)")
    print(f"{len(DROP)} keys dropped across {len(CONFIG['drop_keys'])} groups\n")
    for name, group in CONFIG["drop_keys"].items():
        print(f"  {name:22s} {len(group['keys']):2d} keys")
    print(f"\nempty values: {EMPTY}")
    print(f"artifact: require {REQUIRE_PRESENT}, forbid {REQUIRE_ABSENT}")
