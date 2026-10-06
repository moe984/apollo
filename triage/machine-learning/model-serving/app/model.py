"""Load a frozen release and turn a score into a routing decision."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import joblib
import pandas as pd

from . import preprocess as P

HERE = Path(__file__).resolve().parent.parent
# preprocess.py resolves these first, because it has to load the release's own
# preprocessing contract at import. Reuse them so the two cannot disagree about
# which release is being served.
MODELS = P.MODELS
VERSION = P.VERSION
# Miss budget selects the operating point from the manifest. 0 = the threshold at
# which the release let through no not-benign alerts at all.
MISS_BUDGET = os.environ.get("MISS_BUDGET", "0")
# An explicit override. Set this after recalibrating on a recent window - the
# frozen threshold is measured over twelve months and under-delivers on recent
# traffic. See README.
THRESHOLD_OVERRIDE = os.environ.get("THRESHOLD")


@dataclass(frozen=True)
class Verdict:
    label: str                 # "benign" | "not_benign" | "unscored"
    route_to: str              # "ai_lane" | "human_lane"
    score: float | None        # P(not a Benign Positive)
    threshold: float
    reason: str
    notable_keys: int
    fields_used: int
    input_chars: int


class Router:
    """The frozen model plus the one number that turns its score into an action."""

    def __init__(self, version: str = VERSION, miss_budget: str = MISS_BUDGET,
                 threshold: float | None = None, model_dir: Path = MODELS):
        path = Path(model_dir) / version
        bundle = joblib.load(path / "model.joblib")
        self.manifest = json.loads((path / "manifest.json").read_text())
        self.version = version
        self.model = bundle["model"]
        self.column = bundle["features"][0]

        points = self.manifest["metrics"]["operating_points"]
        self.operating_points = points
        point = points[str(miss_budget)]
        self.miss_budget = str(miss_budget)

        # precedence: explicit argument > THRESHOLD env var > config file >
        # the release's own frozen operating point. The frozen one is the last
        # resort because it is measured across the whole training window and
        # under-delivers badly on recent traffic - 0.41% against 1.65% promised.
        if threshold is not None:
            self.threshold, self.threshold_source = float(threshold), "explicit override"
        elif P.CONFIGURED_THRESHOLD is not None:
            self.threshold = float(P.CONFIGURED_THRESHOLD)
            note = P.THRESHOLD_NOTE
            measured = note.get("measured", {})
            src = f"models/{version}/preprocessing.yaml"
            if note.get("calibrated") is False:
                # v13 and v18 carry their frozen zero-miss point, not a calibrated
                # one. Say so plainly rather than implying a recent measurement.
                self.threshold_source = (
                    f"{src} - {note.get('method', 'uncalibrated')} "
                    f"(auto-closed {measured.get('auto_closed_pct', '?')}%, "
                    f"{measured.get('misses', '?')} misses). "
                    f"NOT calibrated on a recent window - run calibrate.py "
                    f"--version {version} --write before trusting the coverage.")
            else:
                self.threshold_source = (
                    f"{src} - {note.get('method', 'calibrated')}, "
                    f"tolerance {note.get('tolerance', '?')}, "
                    f"calibrated {note.get('calibrated_at')} on "
                    f"{measured.get('on_window', '?')} "
                    f"(auto-closed {measured.get('auto_closed_pct', '?')}%, "
                    f"{measured.get('misses', '?')} misses)")
        else:
            self.threshold, self.threshold_source = point["threshold"], (
                f"frozen manifest, miss_budget={miss_budget} - NOT calibrated")
        self.threshold_is_frozen = (threshold is None
                                    and P.CONFIGURED_THRESHOLD is None)
        self.expected_ai_share_pct = point["ai_share_pct"]

    # ----------------------------------------------------------------- score
    def score_text(self, text: str) -> float:
        """P(this alert is NOT a Benign Positive)."""
        return float(self.model.predict_proba(
            pd.DataFrame({self.column: [text]}))[:, 1][0])

    def score_many(self, texts: list[str]) -> list[float]:
        if not texts:
            return []
        return [float(p) for p in self.model.predict_proba(
            pd.DataFrame({self.column: texts}))[:, 1]]

    # ---------------------------------------------------------------- decide
    def decide(self, record: dict) -> Verdict:
        """Apollo record -> notable -> text -> score -> label."""
        notable = P.notable_of(record)
        if notable is None:
            # 0.17% of Apollo records carry no soar_cef. to_text would return ""
            # and the model would score the empty string near the base rate, so
            # this is refused rather than guessed.
            return Verdict("unscored", "human_lane", None, self.threshold,
                           "no ES notable in the payload", 0, 0, 0)

        text = P.to_text(notable)
        if not text:
            return Verdict("unscored", "human_lane", None, self.threshold,
                           "every field was dropped by preprocessing",
                           len(notable), 0, 0)

        score = self.score_text(text)
        benign = score < self.threshold
        used = sum(1 for k in notable if k not in P.DROP)
        return Verdict(
            label="benign" if benign else "not_benign",
            route_to="ai_lane" if benign else "human_lane",
            score=round(score, 6),
            threshold=self.threshold,
            reason=("below the threshold - confidently a Benign Positive" if benign
                    else "at or above the threshold - needs a human"),
            notable_keys=len(notable),
            fields_used=used,
            input_chars=len(text),
        )

    # ------------------------------------------------------------------ meta
    def info(self) -> dict:
        data = self.manifest["data"]
        return {
            "version": self.version,
            "frozen_at": self.manifest["frozen_at"],
            "task": self.manifest["task"],
            "decision_rule": self.manifest["decision_rule"],
            "input_column": self.column,
            "threshold": self.threshold,
            "threshold_source": self.threshold_source,
            "expected_ai_share_pct": self.expected_ai_share_pct,
            "metrics": {k: self.manifest["metrics"][k]
                        for k in ("roc_auc", "pr_auc", "base_rate_pct", "test_rows")},
            "trained_on_rows": data["rows_trained_on"],
            "trained_through": data["trained_through"],
            "source_sha256": data["source_sha256"],
            "fields_dropped": len(P.DROP),
        }
