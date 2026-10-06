# The shipped model

Three releases are live. Everything else was retired; their metrics are in
`models/history.json`.

| | **`ml/v20` — shipped** | `ml/v18` — rollback | `ml/v13` — auditable |
| --- | --- | --- | --- |
| Fields fitted | **937** — what Apollo sends | 1,312 — everything | 326 + 6 entity types |
| ROC AUC | 0.8540 | **0.8541** | 0.8511 |
| PR AUC | 0.6893 | **0.6899** | 0.6861 |
| **Auto-closes at 0 misses** | **1.65%** | 1.37% | 1.00% |
| `min_df` | 1 | 1 | 2 |
| Inference cost | string flattening only | same | + SecureBERT-NER, ~110ms/alert |

All measured out-of-fold over five expanding forward folds, 27,023 rows.

**v20 is v18 restricted to the 937 fields Apollo has actually been observed to
forward** — the intersection of the training corpus and production reality. v18
carries weights for 375 fields that will never arrive at inference.

The restriction costs **0.0001 ROC**, exactly the noise floor, and buys **18% more
coverage at the zero-miss operating point**. The excluded fields are mostly
VirusTotal enrichment (`vt_whois`, `vt_collections_names`) and vendor-specific
keys, each worth under 0.2% of terms.

`config/apollo_keys.json` holds the list and is **recomputed from a fresh Apollo
export at every retraining** by `dataset_v20.py` — the export is one server over
two months, so a field missing from it may be missing from the sample rather than
from the feed.

`min_df=1` is the other thing separating these from v11/v13: keeping terms seen
exactly once grows the vocabulary from 168,679 to ~919,000 and is worth
**67% more zero-miss coverage**. Singleton rule names and host identifiers are
what let the model be *certain* an alert is benign, and certainty at the low tail
is the only region the router uses.

## What the model is

**Logistic regression over TF-IDF.** `SGDClassifier(loss="log_loss")` is logistic
regression fitted by stochastic gradient descent.

```
Pipeline
├── ColumnTransformer → TfidfVectorizer   (one text column)
│     word 1–2 grams · min_df=2 · sublinear_tf · strip_accents=unicode
│     168,679 terms (v11)
└── SGDClassifier
      loss=log_loss · penalty=l2 · alpha=1e-5 · average=True
      max_iter=10000 · tol=1e-6 · seed=7 · no calibration
```

Why each piece:

- **`log_loss`** — the router needs a probability to threshold, not a margin.
- **`min_df=1`** — see above; +0.0018 ROC and +67% zero-miss coverage.
- **`average=True`** — averaging the weight vector across updates closes most of
  the gap to a batch solver on sparse text. Measured: batch `LogisticRegression`
  scores 0.8445, this scores 0.8523.
- **no calibration wrapper** — tested; a sigmoid calibrator cost 0.03 ROC.
- **linear, not trees** — 918,956 terms at ~257 non-zeros per row. Trees have to
  rediscover each term by splitting on it.

## What it classifies

**Is this alert a Benign Positive, or is it something else?**

| Disposition | Class | `not_benign` | Rows |
| --- | --- | ---: | ---: |
| Benign Positive — Suspicious But Expected | benign | 0 | 25,963 |
| True Positive — Suspicious Activity | not benign | 1 | 5,723 |
| False Positive — Incorrect Analytic Logic | not benign | 1 | 580 |
| False Positive — Inaccurate Data | not benign | 1 | 161 |
| `Other`, `Undetermined`, never dispositioned | *dropped* | — | 91,468 |

Routing follows from the classification: **benign → the AI closes it; not benign
→ a human looks.** The model outputs `P(not benign)` and a threshold decides.

False Positive sits with True Positive because it still needs a human *action* —
a detection fix rather than a response — and keeping class 0 to Benign Positive
alone is what makes the negative class mean one clean thing.

## What it reads

One text column, built from the **ES notable** — the artifact that opened the
container, selected by `rule_id` present **and** `SA_DISPOSITION` absent. The
intuitive test ("no `soar_event` key") is wrong: two of the four artifact
families lack it, and one of those carries the label itself.

Every surviving field becomes `key value`, lowercased, space-joined. 1,312
distinct keys across the corpus, a median of 41 per alert. Full inventory:
`model-training-pipeline/TRAINING_KEYS.md`.

Dropped: 44 keys (Splunk plumbing, per-event identifiers, ES triage state), every
value matching a date/epoch/clock time, and values reading `none`/`null`/`-`/
`unknown`.

## Apollo alignment

Apollo forwards the ES notable itself, in `soar_cef` — not a summary of it. Two
keys exist on one side only and are dropped so training and serving agree:

| Key | Where | Effect if kept |
| --- | --- | --- |
| `extract_artifacts` | SOAR only | a near-constant config blob, 0.89% of terms |
| `splunk_query` | Apollo only, 46% of records | nothing to train on |

With both dropped, the string built from the SOAR export and the string built
from a live Apollo record are **byte-identical on all 3,533 validated alerts, max
score difference `0.00e+00`**.

```python
import joblib, pandas as pd
bundle  = joblib.load("models/v20/model.joblib")
notable = apollo_record["soar_cef"][0]        # always the ES notable
text    = dataset_v11.to_text(notable)        # the same function used in training
p       = bundle["model"].predict_proba(pd.DataFrame({"text_cef": [text]}))[:, 1]
benign  = p < THRESHOLD
```

---

## Two things to handle before deploying

**Recalibrate the threshold.** The manifest's zero-miss threshold comes from
out-of-fold scores across twelve months. Recent traffic runs at a 24.4%
not-benign rate against the 21.7% average, and the deployment model's minimum
score on the recent window sits *above* the published threshold — so it would
auto-close **nothing** on day one while the manifest advertises 0.84%. Take the
operating point from a window that looks like what it will see.

**Guard on the payload.** 0.17% of Apollo records carry no `soar_cef`. `to_text`
returns `""` and the model scores the empty string near the base rate rather than
refusing. Check the artifact exists and route to a human when it does not.

## The ceiling

Group the held-out window by `(rule, tenant)` and give a model the true
not-benign rate of every group — a perfect model no feature set can beat:

```
(rule, tenant) groups with ≥20 alerts        103
groups where verdicts split 5–95%             58   (70.7% of those alerts)
irreducible error inside them               22.5%

a PERFECT model scores                       82.6%
this model scores                            80.2%
```

**2.4 points from the oracle.** Nine feature sets, seven estimators, two
transformer encoders and two hybrids all land between 0.83 and 0.85, because on
70% of the volume the same rule at the same customer is closed both ways by
different analysts. That disagreement is not in the alert.

Further modelling has a measured expected return of approximately zero. What
would move it, in order:

1. **Adjudicate the 58 contested `(rule, tenant)` groups** — the only lever on
   the 82.6% oracle.
2. **Rule-level auto-close** — 21 rules are ≥98% benign and cover **14.6% of the
   queue**, seventeen times what the model takes at zero misses, with no model at
   all.
3. **Give the model the investigation evidence** the analyst sees. It reads the
   alert; the analyst pivots into Splunk.

## Files

| | |
| --- | --- |
| `models/v20/`, `v18/`, `v13/` | the live releases — model, manifest, README |
| `config/preprocessing.yaml` | the **working** rules — the 44 dropped keys in 7 groups, with reasons. What the next retrain reads |
| `models/<release>/preprocessing.yaml` | each release's **frozen** contract: those rules as they stood, its feature definition (min_df, key list) and its own operating threshold. **Serving reads this**, resolved from `MODEL_VERSION` |
| `config/apollo_keys.json` | the 937 fields Apollo sends, recomputed each retrain; copied into `models/v20/` when the release is cut |
| `models/history.json` | metrics, feature definitions and operating points of all 15 retired releases |
| `dataset12.py` | the export → the shared feature table |
| `dataset_v11.py` | the notable → text rule and the Apollo alignment |
| `dataset_v20.py` | the Apollo/training field intersection, recomputed each retrain |
| `cefconfig.py` | loads the working copy, and `for_release(v)` loads a frozen one; every manifest records its rules sha256 |
| `model-serving/` | the FastAPI service — one endpoint, `POST /predict` |
| `dataset_v12.py`, `dataset_v13.py` | v13's coverage floor and entity rendering |
| `freeze.py` | cut a release; `--verify` re-scores every one against its fingerprint |
| `FEATURES.md` | what is fitted, why, and what was rejected |
| `APOLLO_MAPPING.md` | what Apollo sends, measured by value matching |
| `model-training-pipeline/` | the whole pipeline as a Jupyter notebook, plus EDA |
