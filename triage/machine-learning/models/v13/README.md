# Router v13

Frozen 2026-09-24T07:10:48+00:00 - deployment (all rows), trained on all 32,427 rows; thresholds from out-of-fold scores over five expanding forward folds. Do not edit
anything in this directory; cut a new version instead.

```python
import joblib
b = joblib.load("models/v13/model.joblib")
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

ROC AUC 0.8511 · PR AUC 0.6861 · base rate 21.73%

| Miss budget | Threshold | AI takes | Missed | Residual |
| --- | ---: | ---: | ---: | ---: |
| 0 | 0.001540 | 1.0% | 0 | 0.00% |
| 1 | 0.001558 | 1.0% | 1 | 0.36% |
| 2 | 0.001564 | 1.0% | 1 | 0.36% |
| 5 | 0.001955 | 1.9% | 5 | 0.98% |
| 10 | 0.002901 | 4.1% | 10 | 0.90% |
| 20 | 0.003895 | 5.8% | 20 | 1.27% |
| 50 | 0.007447 | 11.2% | 50 | 1.65% |

Calibrated: predicted against actual by score fifth runs 1/2, 3/6, 7/13, 21/27, 63/62.

## Provenance

Verify this artifact is the one that was measured:

```bash
.venv/bin/python freeze.py --verify
```

It re-scores the held-out window and compares against `predictions_sha256` in
`manifest.json`.
