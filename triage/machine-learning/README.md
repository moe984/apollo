# Disposition router

A binary classifier that decides whether an alert goes to an **AI analyst** or a
**human analyst**.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python dataset12.py      # -> dataset12.parquet
.venv/bin/python sgd_model.py      # the regularisation sweep
.venv/bin/python freeze.py         # -> models/vN, a holdout release
.venv/bin/python freeze.py --full  # -> models/vN, a deployment release
.venv/bin/python freeze.py --verify
```

Feature documentation — what the model reads, what is dropped and why, and every
feature tested and rejected — is in **[FEATURES.md](FEATURES.md)**.

`training.ipynb` is the same pipeline end to end — source, leakage check, table,
split, fit, out-of-fold evaluation, freeze — with the outputs saved. It imports
these modules rather than reimplementing them, so it cannot drift from what
ships. Its freeze cell is off by default.

## The data

`dataset12.parquet` — **32,427 verdicts**, 2025-10 → 2026-09, built by
`dataset12.py` from `../soar-containers-12mo-20260923.jsonl.gz`: three SOAR
servers, 56 customers, 437 rules, 19.9% human-lane.

The source is 123,897 containers, one JSON object per line. `read_source()`
streams it and keeps the 32,429 that carry a usable verdict, because the CEF
arrays are the bulk of the 117 MB and there is no reason to hold the rest.

### The training data, as CSV

`export_training_data.py` writes both sides of the transformation to
`training-data/`, so it can be audited without running anything:

| File | Rows | What it holds |
| --- | ---: | --- |
| `original.csv` | 32,427 | the ES notable artifact exactly as it arrived - every key, nothing dropped, nothing scrubbed, as `notable_json` |
| `preprocessed.csv` | 32,427 | `text_cef` - the single column the estimator is fitted on - plus the label, the split and the ordering key |

Same containers, same order, in both. Diffing one row between them shows exactly
what the pipeline removed: the four triage artifacts, the 35 plumbing keys and
every timestamp.

An earlier 90-day CSV extract (10,638 rows, one slice, 27.5% human-lane) was
retired once this superseded it, along with the releases measured on it.

Two things this extract makes possible:

* **`close_time`** — when a human actually ruled the container. The old CSV's
  `_time` was *arrival*; containers marked duplicate, which no analyst ever
  touches, share the worked containers' hour-of-day profile (r = 0.816). Ordering
  by arrival made every forward split approximate. Ordering by the decision means
  "prior-only" says what it means.
* **A real key.** `id` repeats across the three servers — 123,897 containers
  carry only 104,628 distinct ids — so `(server, id)` is the key. The numeric
  `tenant` is server-local too (id 4 is Richline on one server, LSU_HSC_NO on
  another); the customer identity is `tenant_name`, lifted from the CEF
  artifacts, and so are `rule_name` and `search_name`.

### Which artifact is safe to read

A container carries three families of CEF artifact and only one predates the
analyst. Getting this wrong is the easiest way to build a model that scores well
and means nothing:

| Artifact | Identified by | |
| --- | --- | --- |
| ES notable | `rule_id` present, no `soar_event` | the alert as it arrived |
| settings / metrics | carries `SA_DISPOSITION` | **that field is the label** |
| `soar_event=incident_closure` | | carries `closure_disposition` |
| Slack / escalation | `reply_count`, `snooze_count` | only exists if escalated |

Absence of `soar_event` is *not* the test — two of the three unsafe families lack
it. `is_notable()` requires `rule_id` present and `SA_DISPOSITION` absent.

## The label

| Disposition | Lane | y | Rows | Share |
| --- | --- | --- | ---: | ---: |
| Benign Positive - Suspicious But Expected | AI | 0 | 25,963 | 80.1% |
| True Positive - Suspicious Activity | Human | 1 | 5,723 | 17.6% |
| False Positive - Incorrect Analytic Logic | Human | 1 | 580 | 1.8% |
| False Positive - Inaccurate Data | Human | 1 | 161 | 0.5% |
| Other / Undetermined / never dispositioned | *dropped* | — | 91,470 | — |

False positive sits on the human side for three reasons: it needs a human action
(a detection fix, not a response); it is cheap at 2.3% of the queue; and it keeps
the negative class semantically clean. With this split the model learns exactly
one question — **is this the expected, known-benign activity we have seen
before?** — which is what `Benign Positive - Suspicious But Expected` already
means in the source data. Putting FP in the negative class would teach the model
that a broken detection is expected.

`y_tp` (true positive only) ships alongside `y_human` as a free ablation. It
ranks slightly better (ROC 0.876 vs 0.846) but auto-closes false positives, so
it is not the shipping target.

## Two rules for every feature

**Prior-only.** A row's features are built from rows that closed strictly before
it — nothing from the row itself, nothing from its future. The table is therefore
safe to split by time without leaking, and `dataset.py` is the only place this
has to be got right.

**No clock.** No hour, no weekday, no date. `_time` is the *arrival* stamp, not
the decision stamp — containers marked duplicate, which no analyst ever touches,
share the worked containers' hour-of-day profile (r = 0.816). The session effect
it carries is a staffing artifact worth 3.5 points once the benign-streak state
is controlled, and it is one fixed UTC window applied to 54 tenants. A router
that learns the roster fails the quarter the roster changes. `scrub_time` applies
the same rule to the notable blob, stripping dates, epochs and clock times from
every value before it is vectorised.

## Features

The shipping model reads **one column**: `text_cef`, the whole ES notable as
text. `dataset.py` also builds 19 prior-only tabular features that v9 does not
use; they are kept for the leak-free ordering guarantee and for any future stack
that wants history.

See **[FEATURES.md](FEATURES.md)** for the full account, including the 49 notable
keys on 20%+ of alerts, the 35 dropped as plumbing, the time-scrubbing rule, and
the six feature sets that were measured and rejected.

## The release

**`v9` is the only release.** ROC **0.8523** · PR **0.6888**, out-of-fold over
27,023 rows (5,872 human-lane). v4–v8 were deleted once it superseded them; their
metrics, hyperparameters and operating points survive in `models/history.json`,
but the model artifacts do not.

It reads one thing: every field of the Splunk ES notable that opened the
container, flattened to `key value` text and vectorised as word 1–2 grams. That
is the alert exactly as it arrived - the detection that fired, and the entities it
fired on.

| Miss rate | Missed | AI takes | Residual in AI lane |
| --- | ---: | ---: | ---: |
| 0% | 0 | 0.87% | 0.00% |
| 0.5% | 29 | 6.72% | 1.60% |
| **1%** | **59** | **13.47%** | **1.62%** |
| 2% | 117 | 21.16% | 2.05% |
| 5% | 294 | 34.12% | 3.19% |
| 8% | 470 | 42.81% | 4.06% |

Calibration by score fifth, predicted against actual: 1/2, 3/6, 7/13, 20/27,
63/62.

## Exactly what is fitted, on one real alert

Container `prod-soar:229895`, in the holdout window — the model below was fitted
on the 22,699 rows that closed before it and has never seen it.

**1 · What arrives.** Five CEF artifacts, 203 keys. Four are discarded: one holds
`SA_DISPOSITION`, one holds `closure_disposition`, and two are Slack escalation
records that only exist because a human was involved. The ES notable is kept —
84 keys.

**2 · What the notable becomes.** One string, 285 words, after dropping 35
plumbing keys and scrubbing every timestamp:

```
clientname ihmvcu cat initialaccess contributing_events_search | savedsearch
"threat - ts - defender - def07 - new high severity alerts - rule" | search
incidentid="28583" destinationusername sherrick@ihmvcu.org detectionsource
officeatp entity_type alert indicator 216.81.249.98 ipaddress 216.81.249.98
label generic notable_type risk_event orig_rule_description this search looks
for high severity alerts from ms defender and azure that have not been
remediated. there is a 12 hour throttle on user + indicator fields to reduce
noise. risk_message ihmvcu - ts - defender - def07 - new high severity alerts -
a potentially malicious url click was detected rule_name ts - defender - def07 -
new high severity alerts security_domain threat severity high sourceaddress
216.81.249.98 title a potentially malicious url click was detected urgency
medium userprincipalname sherrick@ihmvcu.org workbook mdr workbook
```

That single string is the value of `text_cef`. It is the **only** column the
estimator receives.

**3 · What the model sees.** One column in, 168,679 features out:

```
input     (22699, 1)        one text column, dtype str
            |  TfidfVectorizer(word, 1-2 grams, min_df=2, sublinear_tf)
features  (22699, 168679)   scipy csr_matrix
weights    168679           one per term, learned by SGDClassifier
```

This alert becomes a sparse vector with **255 non-zeros** out of 168,679. "One
feature" is the column contract, not the feature count.

**4 · How the score is built.** Every non-zero term contributes
`tf-idf x weight`, and the sum is squashed to a probability:

| Toward human | tf-idf | x weight | = |
| --- | ---: | ---: | ---: |
| `defender` | 0.102 | +1.50 | +0.153 |
| `severity alerts` | 0.117 | +0.81 | +0.095 |
| `search incidentid` | 0.055 | +1.45 | +0.080 |
| `sourceaddress 216` | 0.083 | +0.74 | +0.062 |
| `malicious` | 0.076 | +0.76 | +0.058 |

| Toward AI | tf-idf | x weight | = |
| --- | ---: | ---: | ---: |
| `98` | 0.124 | −1.54 | −0.190 |
| `org` | 0.073 | −1.97 | −0.145 |
| `potentially malicious` | 0.145 | −0.59 | −0.085 |
| `detectionsource officeatp` | 0.066 | −0.96 | −0.064 |

Note `sourceaddress 216` and `98` — the word tokeniser splits `216.81.249.98`
into octets, so the model is reading *parts of an IP address* as independent
terms. Crude, and it is genuinely how the signal arrives.

**5 · The result.** `score = 0.1785`. True label: **True Positive — needs a
human**.

At a textbook 0.5 threshold that is a **miss**. At the router's 1%-miss operating
point the threshold is **0.0087**, and 0.1785 sits far above it, so the alert is
correctly routed to a human.

That gap is the whole argument for reporting an operating table instead of
accuracy. The model is not confident this alert is dangerous — it is confident
enough that it should not be auto-closed, and that is the only judgement the
router is asked to make.

### Why the whole notable beats the identifiers

The superseded `v8` read only `rule_name`, `search_name` and `container_name` and
scored 0.8376 / 0.6688 on the same rows. The gain is cardinality: `rule_name` has
437 distinct values and describes *what fired*; the notable has 30,321 distinct
blobs and describes *what it fired on* - the user, the source address, the host,
the file, the app, and attributes like `user_category=disabled_account` or
`user_watchlist`. A service account that fires nightly and closes benign every
time becomes recognisable as that account.

| Miss budget | v8 took | v9 takes |
| --- | ---: | ---: |
| 1% | 12.8% | **13.5%** |
| 2% | 19.1% | **21.2%** |
| 5% | 30.5% | **34.1%** |

Two consequences worth knowing before deploying it:

* **It memorises entities.** Real usernames, emails and addresses end up in the
  model weights. That is why it works, and it is a PII consideration for a
  persisted artifact. Cold start on a new customer is worse than an
  identifier-only stack would be.
* **It needs the whole notable at inference**, which is absent on 4.9% of
  containers. Those rows score on an empty blob and land near the base rate,
  which routes them to a human — correct, but it is a silent dependency.

### Timestamps are scrubbed, deliberately

The notable carries dates in at least twenty fields (`user_startDate`,
`firstSeen`, `loginTime`, `drilldown_earliest`) and inside filenames and rule
descriptions. The first fit put `2026 04` among its strongest human-lane terms -
the base rate runs from 6.5% in 2025-11 to 25.3% in 2026-09, so a date predicts
the training window and nothing past it. `scrub_time` removes ISO dates, epochs,
bare years and clock times from every value before vectorising. It costs nothing
measurable (0.8525 → 0.8523 ROC), which is the point: the signal was never the
clock.

### Typed entity extraction is worse than the raw blob

Splitting the notable into `user`, `email`, `ip`, `host`, `file` and `hash`
columns and vectorising each separately scores 0.8157 against the blob's 0.8523,
and adding the typed columns *to* the blob drops it to 0.8390. Out-of-fold, the
single strongest type is `ip` (0.7222), then `file` (0.7127) and `user` (0.7044);
`hash` is a coin flip at 0.5056, because `event_hash` is a Splunk per-event
digest and no real file-hash IOC field is populated. Typing throws away every
field the classifier does not recognise, and those turn out to matter.

ROC is not comparable between the two data columns — different test windows over
different populations. What *is* comparable is holding the 12-month test window
fixed and varying how far back training reaches:

| Train window | Rows | ROC | AI% at a 1% miss budget |
| --- | ---: | ---: | ---: |
| full history (2025-10 →) | 22,699 | **0.8050** | **12.4%** |
| from 2026-01 | 20,157 | 0.8013 | 8.2% |
| from 2026-03 | 12,640 | 0.7963 | 8.0% |
| from 2026-05 | 6,245 | 0.7876 | 7.0% |

More history helps monotonically, despite the human-lane base rate drifting from
6.5% in 2025-11 to 25.3% in 2026-09. Truncating to the recent regime costs both
ranking and coverage, so the whole year is trained on. Three times the rows also
supports a looser penalty than the retired 90-day table did.

### What the AI lane costs

Accuracy is the wrong metric, in both directions. Sending every alert to the AI
scores 78.3% — it is the majority class — while missing every real case. Sending
every alert to a human is the only safe policy and scores 21.7%. v9 at a 0.5
threshold scores well above the majority baseline, but that threshold routes most
genuine human-lane work to the AI, so the number is worthless as a decision
criterion.

The question is how much of the queue the AI can take before it starts closing
real work, and the answer is the operating table above. Two notes on reading it:

* **Budgets are proportional.** 1% of 5,872 human-lane cases is 59 containers.
  Absolute budgets are not comparable between tables of different sizes, which is
  why the retired releases' tables in `models/history.json` cannot be read
  side-by-side with this one.
* **Residual is the number to argue about.** At a 1% budget the AI lane still
  contains 1.62% human-lane work. That is the rate at which something real gets
  auto-closed, and it is the figure to put in front of whoever signs off.

### Why the classes are left unbalanced

The split is 80/20 and stays that way. `class_weight="balanced"`, undersampling
the AI lane and oversampling the human lane were all measured out-of-fold, with
every resample confined to the training folds; all three cost ROC (0.8376 ->
0.831 / 0.813 / 0.817) and all three cut coverage at a 1% miss budget (12.5% ->
10.8% / 8.3% / 8.2%).

Balancing does lift F1 at a threshold of 0.5, from 54.9% to 56.6% - but only by
shifting the scores so that 0.5 lands nearer the optimum. Tuning the threshold on
the unbalanced model reaches F1 59.0%, better than any balanced variant's own
best. This router picks its threshold deliberately, so it never paid the cost
balancing claims to fix.

It hurts for two reasons specific to this data. The human lane is where the
15.3-point analyst disagreement lives, so upweighting it upweights the
contradictions; and the router operates in the far left tail, which is exactly
the region reweighting compresses.

## Known limits

- **The base rate drifts.** 6.5% human-lane in 2025-11 against 25.3% in 2026-09.
  Thresholds are read off the whole year, so they sit between the two regimes;
  re-cut them if the mix moves again.
- **The target is noisy.** Closing desks disagree by 15.3 points on the same rule
  at the same customer. That ceiling sits above anything the model can do.
- **Cold start routes to a human.** A rule or customer with no history has NaN
  track-record features and scores near the base rate, which is the correct
  behaviour — but it means a new customer's first weeks are all human.
- **The shipping model reads text only.** v9 uses no history at all, which is
  what makes it stateless to deploy. The tabular track-record features still
  exist in the table; on the retired 90-day data they were worth roughly 0.03
  ROC, at the cost of carrying per-rule state at serving time.
- **It memorises entities.** Usernames, emails and addresses carry real weight.
  That is the source of the gain over an identifier-only stack and a PII
  consideration for a persisted artifact.
