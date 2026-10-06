# Router v20

Frozen 2026-09-24T07:12:05+00:00 - deployment (all rows), trained on all 32,427 rows; thresholds from out-of-fold scores over five expanding forward folds. Do not edit
anything in this directory; cut a new version instead.

```python
import joblib
b = joblib.load("models/v20/model.joblib")
score = b["model"].predict_proba(X[b["features"]])[:, 1]   # P(needs a human)
ai_lane = score < THRESHOLD
```

`X` must be built by `dataset.py`, which is the only place the prior-only
feature rules are enforced.

## Labels

| Disposition | Lane | y |
| --- | --- | --- |
| Benign Positive | AI | 0 |
| True Positive | Human | 1 |
| False Positive | Human | 1 |

## Measured on 27,023 rows not seen in fitting (5872 human-lane)

ROC AUC 0.854 · PR AUC 0.6893 · base rate 21.73%

| Miss budget | Threshold | AI takes | Missed | Residual |
| --- | ---: | ---: | ---: | ---: |
| 0 | 0.001524 | 1.6% | 0 | 0.00% |
| 1 | 0.001536 | 1.7% | 1 | 0.22% |
| 2 | 0.001544 | 1.7% | 2 | 0.43% |
| 5 | 0.002069 | 3.0% | 5 | 0.62% |
| 10 | 0.002448 | 3.9% | 10 | 0.95% |
| 20 | 0.003656 | 6.3% | 20 | 1.18% |
| 50 | 0.006603 | 11.2% | 50 | 1.65% |

Calibrated: predicted against actual by score fifth runs 1/2, 2/6, 7/13, 20/28, 65/62.

## Provenance

Verify this artifact is the one that was measured:

```bash
.venv/bin/python freeze.py --verify
```

It re-scores the held-out window and compares against `predictions_sha256` in
`manifest.json`.
