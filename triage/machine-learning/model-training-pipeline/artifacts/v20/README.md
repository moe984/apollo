# Router v20

Frozen 2026-09-24T16:23:44+00:00 — deployment (all rows), built by `train.ipynb`. Do not edit anything in this directory; cut a new version instead.

```python
import joblib
bundle = joblib.load("artifacts/v20/model.joblib")
p_not_benign = bundle["model"].predict_proba(X[bundle["features"]])[:, 1]
benign = p_not_benign < THRESHOLD   # benign -> AI closes it, else a human
```

## Measured on 27,023 rows (out-of-fold scores, 5 expanding forward folds)

ROC AUC 0.854 · PR AUC 0.6893 · base rate 21.73%

| Miss budget | Threshold | Called benign | Wrong | Residual |
| --- | ---: | ---: | ---: | ---: |
| 0 | 0.001524 | 1.6% | 0 | 0.00% |
| 1 | 0.001536 | 1.7% | 1 | 0.22% |
| 2 | 0.001544 | 1.7% | 2 | 0.43% |
| 5 | 0.002069 | 3.0% | 5 | 0.62% |
| 10 | 0.002448 | 3.9% | 10 | 0.95% |
| 20 | 0.003656 | 6.3% | 20 | 1.18% |
| 50 | 0.006603 | 11.2% | 50 | 1.65% |
