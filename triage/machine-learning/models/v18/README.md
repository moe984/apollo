# Router v18

Frozen 2026-09-24T07:11:27+00:00 - deployment (all rows), trained on all 32,427 rows; thresholds from out-of-fold scores over five expanding forward folds. Do not edit
anything in this directory; cut a new version instead.

```python
import joblib
b = joblib.load("models/v18/model.joblib")
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

ROC AUC 0.8541 · PR AUC 0.6899 · base rate 21.73%

| Miss budget | Threshold | AI takes | Missed | Residual |
| --- | ---: | ---: | ---: | ---: |
| 0 | 0.001445 | 1.4% | 0 | 0.00% |
| 1 | 0.001452 | 1.4% | 1 | 0.27% |
| 2 | 0.001474 | 1.5% | 2 | 0.50% |
| 5 | 0.002146 | 3.1% | 5 | 0.59% |
| 10 | 0.002563 | 4.2% | 10 | 0.88% |
| 20 | 0.003529 | 6.0% | 20 | 1.23% |
| 50 | 0.007022 | 12.0% | 50 | 1.55% |

Calibrated: predicted against actual by score fifth runs 1/2, 2/6, 7/13, 20/28, 65/62.

## Provenance

Verify this artifact is the one that was measured:

```bash
.venv/bin/python freeze.py --verify
```

It re-scores the held-out window and compares against `predictions_sha256` in
`manifest.json`.
