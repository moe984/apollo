# model-serving

FastAPI service for the alert router. **One endpoint.** POST a CEF notable, get
a label, a score, the threshold, every field's fate and the terms that moved it.

```bash
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
open http://localhost:8000/docs
```

## What it does

```
soar_cef[0]          the ES notable, posted as the request body
   │
   ├─ check    is_notable() - rule_id present AND SA_DISPOSITION absent,
   │           so a triage artifact is refused rather than scored
   ├─ drop     44 CEF fields, from models/<version>/preprocessing.yaml
   ├─ drop     fields outside the release's own key list - 937 for v20, none for v18
   ├─ scrub    every timestamp, epoch, bare year and clock time — by value
   ├─ drop     values reading none / null / - / unknown
   ├─ render   the rest to `key value` text, lowercased
   ├─ score    TF-IDF → SGDClassifier  →  P(not a Benign Positive)
   └─ label    score < threshold ? benign : not_benign
```

**Benign Positive → `ai_lane`. Anything else → `human_lane`.**

## The endpoint

```bash
curl -X POST localhost:8000/predict -H 'content-type: application/json' \
     -d @sample_benign.json
```

Body is `soar_cef[0]` itself — the notable object, nothing wrapping it. A whole
Apollo record is tolerated and unwrapped.

```json
{
  "decision":  { "label": "benign", "route_to": "ai_lane",
                 "action": "auto-close - no human review required",
                 "reason": "score 0.000609 is below the threshold 0.001524" },
  "scoring":   { "score": 0.000609, "threshold": 0.001524, "margin": -0.000915,
                 "confidence": "very high",
                 "confidence_note": "far from 0.5 - the model is near certain" },
  "preprocessing": { "keys_received": 69, "keys_used": 35, "keys_dropped": 34,
                     "dropped": { "status": "dropped - triage_state: LEAKAGE ..." },
                     "used":    { "rule_name": "rule_name ts - windows - ..." },
                     "model_input": "clientname nunez destinationusername ...",
                     "input_tokens": 227,
                     "tokens_known_to_model": 193,
                     "tokens_unknown_to_model": 34 },
  "top_terms": [ { "term": "t1030", "contribution": 0.1176 } ],
  "model":     { "version": "v20", "roc_auc": 0.854 },
  "took_ms": 1.2
}
```

`tokens_unknown_to_model` is the retrain signal: terms outside the training
vocabulary contribute nothing. `confidence` is measured against **0.5**, where the
model is ~52% accurate — not against the threshold.

`sample_benign.json` and `sample_not_benign.json` are ready to paste into `/docs`.

`GET /health` exists as a liveness probe and is hidden from the schema.

## Configuration

| Variable | Default | |
| --- | --- | --- |
| `MODEL_VERSION` | `v20` | which release under `models/` to load |
| `MISS_BUDGET` | `0` | operating point from the manifest — 0 is the zero-miss threshold |
| `THRESHOLD` | — | explicit override; **set this after recalibrating** |
| `MODEL_DIR` | `./models` | where releases live |

## ⚠ Recalibrate the threshold before you trust the coverage

The frozen threshold (`0.001445`) was measured **out-of-fold across twelve
months**. The alert population has moved — the not-benign rate ran from 6.5% to
23.3% over the training window — so on recent traffic that threshold is too
strict:

| | Auto-closes | Misses |
| --- | ---: | ---: |
| frozen threshold, applied to recent alerts | **0.42%** | 0 |
| manifest advertises | 1.37% | 0 |
| **recalibrated on a recent window** | **2.05%** | **0** |

Five times the coverage, same zero-miss safety. The model does not change; only
the line through its scores does.

**Protocol** — three-way forward split, never shuffled:

1. **train** on the oldest data (already done — the frozen model)
2. **calibrate**: take a recent labelled window the model never saw, sort by
   score, walk up to the first not-benign alert, put the threshold just below it
3. **validate** on a window *after* that one and quote *those* numbers —
   calibration is always optimistic by a few points of coverage
4. re-run monthly; `THRESHOLD=<value>` to deploy without retraining

Stage 7 of `../model-training-pipeline/train.ipynb` runs this end to end.

## Guardrails

**No notable, no score.** 0.17% of Apollo records carry no `soar_cef`. The
service returns `label: "unscored"`, `route_to: "human_lane"` rather than scoring
an empty string — which the model would otherwise happily rate near the base
rate.

**The artifact is checked, not assumed.** `is_notable()` runs even though Apollo
only ever forwards the notable. If the upstream payload changes to include a
triage artifact, this fails loudly instead of feeding the model a field that
leaks the answer.

## Configuration, not code

The drop rules are **not hard-coded**, and they belong to the release rather
than to the service. `models/<version>/preprocessing.yaml` holds all 44
keys in 7 groups, each with its reason, and is read by the training pipeline and
this service. The same file names the release's key list — `apollo_keys.json`, the 937 fields
v20 was fitted on — and carries that release's operating threshold.

This is why `MODEL_VERSION=v18` is a safe rollback: it picks up v18's rules, v18's
key list (none) and v18's threshold together. Before each release owned its own
config, a rollback would have kept applying v20's 937-key filter and v20's
threshold to v18's scores.
Every release records both in its manifest.

Nothing lists which keys are *allowed*: a CEF key never seen before flows through
preprocessing with no code change. It simply contributes nothing until a retrain,
because the vocabulary has no terms for it — which `tokens_unknown_to_model`
reports on every request.

## Preprocessing parity

`app/preprocess.py` is a **vendored copy** of the training rules, so the service
deploys without the training repo. `tests/test_parity.py` is what stops the two
drifting:

```bash
.venv/bin/python -m pytest tests/ -q
```

10 tests. Against 400 real Apollo records they assert that

- the served `preprocessing.json` is **byte-identical** to training's
- it is the config **the loaded release records in its manifest** — edit the file
  without retraining and this fails
- the served key list equals the one `dataset_v20` computes
- the key filter **actually filters** (Apollo records contain only Apollo keys, so
  a parity test over real traffic cannot see this — it is forced with a fixture)
- **the rendered text is byte-identical** to training's
- **the resulting scores differ by exactly `0.0`**
- a missing or non-notable artifact is refused, not guessed

If you change preprocessing on either side, this test fails. That is the point.

## The model

`models/v20` — TF-IDF (word 1–2 grams, `min_df=1`, sublinear) into an averaged
`SGDClassifier` with log loss. Fitted on the **937 fields Apollo actually sends**,
one weight per term.

Out-of-fold over 27,023 rows: **ROC 0.8540, PR 0.6893**. At the zero-miss
operating point it auto-closes **1.62%** of the queue with no not-benign alerts in
that lane — 18% more than v18, for 0.0001 ROC.

See `../MODEL.md` for the architecture and `../APOLLO_MAPPING.md` for why the
Apollo payload and the training input are the same thing.

## Rebuilding

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/ -q
.venv/bin/uvicorn app.main:app --port 8000
```
