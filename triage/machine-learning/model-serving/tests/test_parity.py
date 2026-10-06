"""The service must build the same string training did - byte for byte.

`app/preprocess.py` is a vendored copy of the training rules. If someone edits
one and not the other, the model silently scores a different string than it was
fitted on and nothing else in the system notices. This test is what notices.
"""
from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
ML = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ML))

from app import preprocess as P            # noqa: E402
from app.model import Router               # noqa: E402

APOLLO = Path("/Users/mohammad.yekrangian/Downloads/"
              "apollo-soar-alerts-20260923.jsonl.gz")
SAMPLE = 400


def apollo_records(limit=SAMPLE):
    if not APOLLO.exists():
        pytest.skip(f"{APOLLO.name} not present")
    out = []
    with gzip.open(APOLLO, "rt") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec.get("soar_cef"):
                out.append(rec)
            if len(out) >= limit:
                break
    return out


def test_release_config_is_identical_to_the_training_copy():
    """The release's served config is byte-identical to the training-side original.

    Each release owns its contract now, so this compares like for like:
    model-serving/models/<v>/preprocessing.yaml against
    machine-learning/models/<v>/preprocessing.yaml.
    """
    served = P.CONFIG_PATH
    trained = ML / "models" / P.VERSION / "preprocessing.yaml"
    assert trained.exists(), f"{trained} missing - re-cut {P.VERSION} with freeze.py"
    assert served.read_bytes() == trained.read_bytes(), (
        f"{P.VERSION} config drift between training and serving. "
        f"Copy {trained} to {served}.")


def test_release_config_rules_match_the_working_rules():
    """The frozen rules still agree with the working config.

    They may legitimately differ once the working copy is edited for the NEXT
    release - that is the point of the split - so this reports rather than
    forbids, and only fails for the release currently being served.
    """
    import cefconfig as CFG
    assert P.CONFIG_SHA == CFG.rules_sha256(CFG.for_release(P.VERSION)), (
        "the served config's rules do not match the frozen release's own file")


def test_config_matches_the_frozen_release():
    """And it is the config the loaded release records in its manifest."""
    from app.model import Router
    r = Router()
    recorded = r.manifest.get("preprocessing", {}).get("config_sha256")
    assert recorded, "release predates config tracking - re-cut it"
    assert recorded == P.CONFIG_SHA, (
        f"the config changed since {r.version} was frozen "
        f"({recorded[:16]} -> {P.CONFIG_SHA[:16]}). Retrain or roll the config back.")


def test_each_release_carries_its_own_contract():
    """Every frozen release must be self-describing - rules, features, threshold.

    Before the split there was one shared config, so serving v18 would silently
    have applied v20's 937-key filter and v20's threshold. This is the guard.
    """
    import cefconfig as CFG
    seen = {}
    for path in sorted((ML / "models").glob("*/manifest.json")):
        release = path.parent.name
        cfg = CFG.for_release(release)          # raises if the file is missing
        feat = cfg.get("features") or {}
        assert feat.get("input_column"), f"{release}: no input_column"
        assert cfg.get("operating_threshold", {}).get("value") is not None, \
            f"{release}: no operating threshold"
        seen[release] = (feat.get("key_list"), feat.get("tfidf_min_df"))
    assert len(seen) >= 2, "expected several releases to compare"
    # the whole point: they are not all the same
    assert len(set(seen.values())) > 1, (
        f"every release claims an identical feature contract {seen} - "
        f"the per-release split is not doing anything")


def test_drop_set_matches_training():
    """The drop set the service applies is exactly the one v18 was trained with."""
    import notable_text as NT
    assert P.DROP == NT.DROP, (
        f"only in service {P.DROP - NT.DROP}, only in training {NT.DROP - P.DROP}")


def test_scrub_matches_training():
    import dataset12 as D12
    for probe in ["2026-04-11T08:13:00.000+00:00", "firstSeen 1790117880.000000000",
                  "login at 08:13:22 on 2026", "backup_2026-04-11.zip",
                  "severity high", "10.1.2.3", "fe80::422b:2ada"]:
        assert P.scrub_time(probe) == D12.scrub_time(probe), probe


def test_key_list_matches_the_release():
    """The served key list is the one the loaded release was fitted on."""
    import dataset_v20 as V20
    from app.model import Router
    if Router().version != "v20":
        pytest.skip("key list only applies to v20")
    assert P.APOLLO_KEYS is not None, "models/v20/apollo_keys.json missing"
    assert P.APOLLO_KEYS == V20.keep_keys(write=False), (
        "served key list differs from the one dataset_v20 computes; "
        "recopy models/v20/apollo_keys.json")


def test_key_filter_actually_filters():
    """A field outside the trained key list must be excluded, not silently kept.

    Apollo records contain only Apollo keys, so the filter is a no-op on real
    traffic and a parity test over them cannot see it. This forces the case.
    """
    notable = {"rule_id": "x@@notable@@t1", "rule_name": "TS - Test",
               "vt_whois": "some whois blob never sent by apollo"}
    assert "vt_whois" not in P.APOLLO_KEYS, "fixture assumes vt_whois is excluded"
    text = P.to_text(notable)
    assert "vt_whois" not in text, "a non-Apollo field reached the model input"
    assert "rule_name" in text
    assert "not in the trained key list" in P.explain(notable)["dropped"]["vt_whois"]


def test_rendered_text_is_byte_identical():
    """The whole point: same notable in, same string out - against v20's renderer."""
    import dataset_v20 as V20
    keep = V20.keep_keys(write=False)
    records = apollo_records()
    mismatched = []
    for rec in records:
        notable = P.notable_of(rec)
        if notable is None:
            continue
        if P.to_text(notable) != V20.to_text(notable, keep):
            mismatched.append((rec.get("result") or {}).get("container_id"))
    assert not mismatched, f"{len(mismatched)} of {len(records)} differ: {mismatched[:5]}"


def test_scores_match_the_training_pipeline():
    """And the model gives the same number either way."""
    import dataset_v20 as V20
    import joblib
    import pandas as pd

    keep = V20.keep_keys(write=False)
    router = Router()
    bundle = joblib.load(ML / "models" / router.version / "model.joblib")
    records = apollo_records(120)
    served, direct = [], []
    for rec in records:
        notable = P.notable_of(rec)
        if notable is None:
            continue
        served.append(router.score_text(P.to_text(notable)))
        direct.append(float(bundle["model"].predict_proba(
            pd.DataFrame({bundle["features"][0]: [V20.to_text(notable, keep)]}))[:, 1][0]))
    assert served, "no scorable records"
    worst = max(abs(a - b) for a, b in zip(served, direct))
    assert worst == 0.0, f"max score difference {worst:.3e} over {len(served)} alerts"


def test_missing_notable_is_refused_not_guessed():
    router = Router()
    for bad in ({}, {"soar_cef": []}, {"soar_cef": [{"soar_event": "incident_closure"}]}):
        v = router.decide(bad)
        assert v.label == "unscored"
        assert v.route_to == "human_lane"
        assert v.score is None


def test_label_follows_the_threshold():
    router = Router()
    for rec in apollo_records(60):
        v = router.decide(rec)
        if v.score is None:
            continue
        assert (v.label == "benign") == (v.score < v.threshold)
        assert (v.route_to == "ai_lane") == (v.label == "benign")
