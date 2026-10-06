"""One endpoint. Post a CEF notable, get everything.

    POST /predict     the ES notable object itself as the body

Returns the decision, the score, the threshold it was compared against, how
confident the model is, every field and what happened to it, the exact string the
model read, and the terms that moved the score.

    .venv/bin/uvicorn app.main:app --port 8000
    open http://localhost:8000/docs

Environment:
    MODEL_VERSION   which frozen release to load        (default v18)
    MISS_BUDGET     operating point from the manifest   (default 0 = zero-miss)
    THRESHOLD       explicit override; set this after recalibrating
"""
from __future__ import annotations

import time
from typing import Any

import numpy as np
import pandas as pd
from fastapi import Body, FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import preprocess as P
from .model import Router

app = FastAPI(
    title="AI-SOC alert router",
    version="2.0.0",
    description=(
        "**POST a CEF notable. Get a routing decision.**\n\n"
        "Classifies a Splunk ES notable as *Benign Positive* or not. "
        "Benign Positive -> the AI closes it; anything else -> a human looks.\n\n"
        "The model returns `P(not benign)`; the threshold turns that into a label."
    ),
)
router: Router | None = None


@app.on_event("startup")
def _load() -> None:
    global router
    router = Router()


# ------------------------------------------------------------------ response
class Decision(BaseModel):
    label: str = Field(..., description="benign | not_benign | unscored")
    route_to: str = Field(..., description="ai_lane | human_lane")
    action: str = Field(..., description="what the lane means in practice")
    reason: str


class Scoring(BaseModel):
    score: float | None = Field(..., description="P(not a Benign Positive), 0-1")
    threshold: float = Field(..., description="below this the alert is called benign")
    margin: float | None = Field(..., description="score - threshold; negative means benign")
    confidence: str = Field(..., description="how far the score sits from 0.5, where the model is blind")
    confidence_note: str


class Preprocessing(BaseModel):
    keys_received: int
    keys_used: int
    keys_dropped: int
    dropped: dict[str, str] = Field(..., description="every dropped key and why")
    used: dict[str, str] = Field(..., description="every kept key and the text it contributed")
    model_input: str = Field(..., description="the exact string handed to the model")
    input_tokens: int
    tokens_known_to_model: int
    tokens_unknown_to_model: int = Field(..., description="terms outside the training vocabulary; they contribute nothing")


class Term(BaseModel):
    term: str
    contribution: float = Field(..., description="signed push on the score; + toward not_benign")


class Model(BaseModel):
    version: str
    frozen_at: str
    roc_auc: float
    pr_auc: float
    threshold_source: str
    expected_ai_share_pct: float


class Response(BaseModel):
    decision: Decision
    scoring: Scoring
    preprocessing: Preprocessing
    top_terms: list[Term] = Field(..., description="what moved the score, most influential first")
    model: Model
    took_ms: float


EXAMPLE = {
    "rule_id": "f2098555-4a50-4197-84d3-30d4e21720a8@@notable@@time1786109283",
    "rule_name": "TS - Windows - Multiple User Creations Within Short Time",
    "rule_title": "$ClientName$ - Windows - Multiple User Creations Within Short Time",
    "search_name": "Audit - TS - Windows - Multiple User Creations Within Short Time - Rule",
    "rule_description": "This search looks for over 10 user creations in a short timespan.",
    "savedsearch_description": "This search looks for over 10 user creations in a short timespan.",
    "security_domain": "audit", "severity": "low", "severities": "low", "urgency": "low",
    "notable_type": "notable", "label": "generic", "eventtype": ["modnotable_results", "notable"],
    "ClientName": "nunez", "workbook": "MDR Workbook", "orig_action_name": "notable",
    "destinationUserName": ["adamagurcia", "amilyamullen", "andreaherrera3"],
    "source": "Audit - TS - Windows - Multiple User Creations Within Short Time - Rule",
    "_bkt": "notable~348~2B59E839", "_cd": "348:255",
    "_time": "2026-08-07T13:28:03.000+00:00", "status": "1", "owner": "unassigned",
    "date_hour": "13",
}


def _band(score: float) -> tuple[str, str]:
    """Confidence is distance from 0.5, not distance from the threshold."""
    d = abs(score - 0.5)
    if d > 0.45:
        return "very high", "far from 0.5 - the model is near certain"
    if d > 0.35:
        return "high", "well clear of 0.5"
    if d > 0.20:
        return "moderate", "leaning, but not certain"
    return "low", ("close to 0.5, where this model is measured at ~52% accuracy - "
                   "worth a careful human look regardless of the label")


@app.post("/predict", response_model=Response,
          summary="Score one CEF notable and explain the result")
def predict(notable: dict[str, Any] = Body(..., examples=[EXAMPLE])) -> Response:
    """Post the ES notable object itself - `soar_cef[0]` - as the request body."""
    if router is None:
        raise HTTPException(503, "model not loaded")
    t0 = time.perf_counter()

    if not isinstance(notable, dict) or not notable:
        raise HTTPException(422, "body must be a non-empty CEF object")
    # tolerate a whole Apollo record being posted by mistake
    if "soar_cef" in notable:
        cef = notable.get("soar_cef") or []
        if not cef:
            raise HTTPException(422, "soar_cef is empty - no notable to score")
        notable = cef[0]

    if not P.is_notable(notable):
        raise HTTPException(
            422,
            "this is not an ES notable: it must carry `rule_id` and must not carry "
            "`SA_DISPOSITION` or `soar_event`. Those other artifacts are written "
            "while the container is worked and would leak the verdict.")

    detail = P.explain(notable)
    text = P.to_text(notable)
    if not text:
        raise HTTPException(422, "every field was dropped by preprocessing")

    score = router.score_text(text)
    benign = score < router.threshold
    band, note = _band(score)

    # which terms moved it
    ct = router.model.named_steps["features"]
    sgd = router.model.named_steps["sgd"]
    vec = ct.transformers_[0][1]
    X = ct.transform(pd.DataFrame({router.column: [text]}))
    idx = X.nonzero()[1]
    inv = {v: k for k, v in vec.vocabulary_.items()}
    pull = sorted(((inv[i], float(X[0, i] * sgd.coef_[0][i])) for i in idx),
                  key=lambda t: -abs(t[1]))[:12]

    tokens = text.split()
    known = sum(1 for t in tokens if t in vec.vocabulary_)

    return Response(
        decision=Decision(
            label="benign" if benign else "not_benign",
            route_to="ai_lane" if benign else "human_lane",
            action=("auto-close - no human review required" if benign
                    else "send to a human analyst"),
            reason=(f"score {score:.6f} is below the threshold {router.threshold:.6f}"
                    if benign else
                    f"score {score:.6f} is at or above the threshold {router.threshold:.6f}"),
        ),
        scoring=Scoring(
            score=round(score, 6), threshold=router.threshold,
            margin=round(score - router.threshold, 6),
            confidence=band, confidence_note=note,
        ),
        preprocessing=Preprocessing(
            keys_received=len(notable),
            keys_used=detail["n_kept"], keys_dropped=detail["n_dropped"],
            dropped=detail["dropped"], used=detail["kept"],
            model_input=text, input_tokens=len(tokens),
            tokens_known_to_model=known,
            tokens_unknown_to_model=len(tokens) - known,
        ),
        top_terms=[Term(term=t, contribution=round(c, 6)) for t, c in pull],
        model=Model(
            version=router.version,
            frozen_at=router.manifest["frozen_at"],
            roc_auc=router.manifest["metrics"]["roc_auc"],
            pr_auc=router.manifest["metrics"]["pr_auc"],
            threshold_source=router.threshold_source,
            expected_ai_share_pct=router.expected_ai_share_pct,
        ),
        took_ms=round((time.perf_counter() - t0) * 1000, 2),
    )


@app.get("/health", include_in_schema=False)
def health() -> dict:
    return {"status": "ok", "model_version": router.version if router else None}
