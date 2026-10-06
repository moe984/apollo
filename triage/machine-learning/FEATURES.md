# Features

What `ml/v9` reads, why, and what was deliberately left out.
`ml/v10` reads a narrower, named slice of the same alert - see [v10](#v10-the-named-slice) at the end.

## The label

| Disposition | Lane | `y_human` | Rows |
| --- | --- | ---: | ---: |
| Benign Positive - Suspicious But Expected | AI | 0 | 25,963 |
| True Positive - Suspicious Activity | Human | 1 | 5,723 |
| False Positive - Incorrect Analytic Logic | Human | 1 | 580 |
| False Positive - Inaccurate Data | Human | 1 | 161 |
| `Other`, `Undetermined`, never dispositioned | *dropped* | — | 91,470 |

False positive sits on the human side because it needs a human action - a
detection fix, not a response - and because it keeps the negative class
semantically clean. With this split the model learns one question: **is this the
expected, known-benign activity we have seen before?**

## The input: one column

`ml/v9` consumes **`text_cef`** and nothing else. Its manifest says so:

```json
"features": ["text_cef"],
"input_column": "text_cef"
```

`text_cef` is the entire Splunk ES notable artifact - the alert exactly as it
arrived - flattened to `key value` text.

| | |
| --- | ---: |
| Non-empty | 95.1% of alerts |
| Distinct values | 30,229 of 32,427 |
| Words per alert (median) | 228 |
| TF-IDF vocabulary | 168,679 terms |
| Non-zeros per row | 276 |
| Matrix density | 0.163% |

### Step 1 - pick the right artifact

A container carries three families of CEF artifact. **Only one predates the
analyst**, and getting this wrong is the easiest way to build a model that scores
well and means nothing.

| Artifact | Identified by | Used? | |
| --- | --- | --- | --- |
| **ES notable** | `rule_id` present, no `soar_event` | **yes** | the alert as it arrived |
| settings / metrics | carries `SA_DISPOSITION` | no | **that field *is* the label** |
| `soar_event=incident_closure` | | no | carries `closure_disposition` |
| Slack / escalation | `reply_count`, `snooze_count` | no | only exists if escalated |

Absence of `soar_event` is **not** the test - two of the three unsafe families
lack it too. `is_notable()` requires `rule_id` present **and** `SA_DISPOSITION`
absent.

A worked example is in `soar/def07_cef_sample.txt`: five artifacts, 203 keys, of
which four artifacts contain the verdict, the analyst's reasoning, the escalation
chain, or a written investigation summary.

### Step 2 - flatten to `key value` text

Fields are emitted as `key value` pairs so a word bigram can bind a field to its
reading: `severity low` stays distinct from `urgency low`. Values that are lists
are space-joined; `none`, `null`, `-` and `unknown` are dropped.

### Step 3 - scrub anything that encodes *when*

`scrub_time()` removes ISO dates, epochs, bare years and clock times from every
value, not by key but by pattern, because dates are embedded inside filenames and
rule descriptions as well as in ~20 dedicated fields (`user_startDate`,
`firstSeen`, `loginTime`, `drilldown_earliest`, ...).

This is not optional. The human-lane base rate runs from **6.5% in 2025-11 to
25.3% in 2026-09**, so a date predicts the training window and nothing past it -
the first fit put `2026 04` among its strongest human-lane terms. Scrubbing costs
**0.0002 ROC**, which is the point: the signal was never the clock.

### Step 4 - drop the plumbing

35 keys are removed before vectorising - Splunk internals and per-alert
identifiers that can only ever be noise or a memorised handle on one row:

`_bkt` `_cd` `_si` `_indextime` `_sourcetype` `_time` `_eventtype_color`
`event_id` `rule_id` `orig_sid` `orig_rid` `orig_time` `source_event_id`
`info_max_time` `info_min_time` `info_search_time` `timestamp` `time`
`event_hash` `es_notable_url` `ES_NOTABLE_URL` `container_url` `ES_URL`
`date_hour` `date_mday` `date_wday` `date_year` `date_minute` `date_month`
`date_second` `date_zone` `post_time` `receipt_time` `sla_receipt`
`status` `status_default` `status_end` `status_group` `status_label`
`status_description` `owner` `owner_realname`

`status` and `owner` describe ES-side triage state, which moves after arrival.

### Step 5 - vectorise

```python
TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=2,
                sublinear_tf=True, strip_accents="unicode", lowercase=True)
```

`min_df=2` is load-bearing: an entity seen exactly once is discarded, so only
**recurring** users, hosts and subnets get a weight.

## What survives, by coverage

**Every key on the notable goes in. There is no coverage threshold.** 1,398
distinct keys reach the blob across 30,830 notables, and only 15 of them are on
100% of alerts:

| Coverage band | Keys |
| --- | ---: |
| 100% | 15 |
| 50-100% | 14 |
| 20-50% | 20 |
| 5-20% | 56 |
| 1-5% | 243 |
| under 1% | 1,050 |

Three quarters of the keys appear on fewer than one alert in a hundred, and they
carry real weight - `filename` is on 8.3% of alerts and scores +1.04, one of the
stronger terms in the model. The only rarity filter is `min_df=2`, and it acts on
*terms* rather than keys: it discards a username seen exactly once while keeping
the `filename` key that introduced it.

That long tail is source-specific - Defender fields on Defender alerts,
CrowdStrike fields on CrowdStrike alerts, Azure fields on Azure alerts. A fixed
schema would have to drop it. This is the likeliest reason the raw blob beats
every curated feature set tried below: hand-picking fields throws the tail away.

The table below is **documentation of what is in there, not a selection rule**.
It is cut at 20% only because 1,398 rows would be unreadable. Those keys:

| Key | Coverage |
| --- | ---: |
| `label` | 100.0% |
| `source` | 100.0% |
| `urgency` | 100.0% |
| `priority` | 100.0% |
| `severity` | 100.0% |
| `workbook` | 100.0% |
| `eventtype` | 100.0% |
| `rule_name` | 100.0% |
| `rule_title` | 100.0% |
| `severities` | 100.0% |
| `search_name` | 100.0% |
| `notable_type` | 100.0% |
| `security_domain` | 100.0% |
| `orig_action_name` | 100.0% |
| `rule_description` | 100.0% |
| `extract_artifacts` | 95.6% |
| `investigation_profiles` | 95.6% |
| `savedsearch_description` | 95.4% |
| `destinationUserName` | 81.0% |
| `timeendpos` | 79.9% |
| `timestartpos` | 79.9% |
| `source_guid` | 79.8% |
| `orig_rule_title` | 78.9% |
| `orig_security_domain` | 78.8% |
| `risk_score` | 78.7% |
| `orig_rule_description` | 78.6% |
| `user_risk_score` | 66.1% |
| `user_risk_object_type` | 66.1% |
| `sourceAddress` | 52.9% |
| `queue_id` | 44.5% |
| `ClientName` | 42.4% |
| `count` | 39.9% |
| `cat` | 33.7% |
| `detection_type` | 33.3% |
| `orig_investigation_type` | 33.0% |
| `orig_status` | 29.7% |
| `act` | 27.7% |
| `sourceUserName` | 26.6% |
| `title` | 23.9% |
| `incidentId` | 23.2% |
| `risk_object_type` | 22.9% |
| `risk_object` | 22.9% |
| `normalized_risk_object` | 22.9% |
| `detectionSource` | 22.1% |
| `ipAddress` | 21.8% |
| `entity_type` | 20.8% |
| `contributing_events_search` | 20.8% |
| `entity` | 20.8% |
| `risk_message` | 20.5% |

All 1,349 keys below this cut are in the model too.

## What it learned

| Toward **human** | | Toward **AI** | |
| --- | ---: | --- | ---: |
| `10 100` | +4.39 | `168 253` | −2.25 |
| `password` | +3.42 | `deleted` | −2.10 |
| `255` | +3.07 | `windows` | −2.08 |
| `spraying detected` | +2.95 | `117` | −2.10 |
| `hosting provider` | +2.63 | `org` | −1.97 |

The bare numbers are **IP octets** - the word tokeniser splits `165.225.36.87`
into `165 225 36 87`, so `10 100` means *the 10.100.x.x subnet*. Crude
tokenisation, but it is how the signal arrives.

**This memorises entities.** Real usernames, emails and addresses carry weight.
That is precisely why v9 beats an identifier-only stack, and it is a PII
consideration for a persisted artifact.

## In the table but unused

`dataset.py` builds 19 prior-only tabular features. **v9 reads none of them.**
They are kept because they enforce the leak-free ordering guarantee and because
any future stack that wants history starts here.

| Group | Columns |
| --- | --- |
| This customer + this rule | `pair_seen`, `pair_benign_run`, `pair_last`, `pair_human_rate` |
| The rule's track record | `rule_human_rate`, `rule_human_raw`, `rule_tp_rate`, `rule_n`, `rule_flip_rate`, `rule_flip_n`, `rule_tenants` |
| The customer | `tenant_human_rate`, `tenant_n` |
| The alert title | `title_human_rate`, `title_n` |
| The container | `name_len`, `name_parts` |
| Identity *(excluded from every model)* | `rule`, `tenant` |
| Other text | `text_name`, `text_rule`, `text_title`, `text_rule_raw`, `text_search` |

Every rate is prior-only: row *i* uses only rows that closed strictly before it.
Rates are Laplace-smoothed toward the base rate (`laplace_n=20`) so a rule with
two observations cannot claim 0% or 100%.

Identity (`rule`, `tenant`) is excluded on evidence: including it drops ROC from
0.840 to 0.806. It memorises the training window and does not survive the move
forward.

## Features tested and rejected

Every one of these was measured out-of-fold against a noise floor of **0.0001
ROC** and **0.19pp coverage**, established by a five-seed sweep.

| Added | ROC | Coverage @ 1% miss | Verdict |
| --- | ---: | ---: | --- |
| severity + urgency + security_domain + risk_score | +0.0008 | **−1.4pp** | rejected |
| typed entities (ip, user, email, host, file, hash) | −0.037 | −2.1pp | rejected |
| `rule_description` | +0.0011 | +0.14pp (noise) | rejected |
| `orig_rule_description` + `rule_name` + `title` | −0.008 | −0.6pp | rejected |
| char n-grams on `container_name` | +0.003 | mixed | untested at seed level |
| class balancing (weights / under / over) | −0.007 to −0.024 | −1.7 to −4.3pp | rejected |

**The pattern is consistent and worth stating plainly: every attempt to help the
blob by re-supplying its own contents failed.** All of those fields are already
inside `text_cef`. Giving one its own TF-IDF block does not add information, it
overrides the IDF weighting that the corpus already computed - and makes it
worse.

Two structural findings from the same series:

- **Typed entity extraction loses to the raw blob** (0.8157 vs 0.8523). Typing
  discards every field the classifier does not recognise, and `user_category`,
  `user_watchlist`, `app` and `signature` turn out to matter.
- **Context length is not the constraint.** Feeding a transformer the whole alert
  in 512-token windows scored 0.7596 against 0.7588 truncated - +0.0008. See
  `transformers/experiments.json`.

## The ceiling

Every architecture tried lands in a **0.76-0.85** band: TF-IDF, gradient-boosted
trees, ATTACK-BERT frozen, ATTACK-BERT fine-tuned, embeddings into SGD. Closing
desks disagree by **15.3 points** on the same rule at the same customer, which
sits above all of them.

The label noise is the ceiling, not the feature set. More features will not clear
it; better labels would.

## v10: the named slice

`ml/v9` fits the whole notable - 1,398 distinct keys, values and all - and scores
**0.8523**. That works, but its weights encode whatever strings happen to separate
one alert from the next, which includes real accounts and addresses.

`ml/v10` asks how much of that is carried by things you can name. It reads
**`text_v10`**, built by `dataset_v10.py` from two parts.

### Part 1 - the rule's own words

Five fields of the ES notable, laid out as `key value` like v9's blob, time-scrubbed
by the same `scrub_time`.

| Field | Coverage | What it is |
| --- | ---: | --- |
| `rule_name` | 95.1% | which detection fired |
| `rule_description` | 95.1% | what the detection looks for - static per rule |
| `orig_rule_description` | 74.7% | the same sentence instantiated for this alert, so it names the host and the file |
| `rule_title` | 95.1% | the rule's headline; differs from `rule_name` on 83% of rows |
| `title` | 21.2% | the vendor product's own headline, when the alert came from one |

### Part 2 - entities at >= 0.999

SecureBERT-NER v2 run locally over the rendered notable, one pass, captured in
`training-data/entities.csv`. Eight labels are kept: **EMAIL, IPV4, IPV6,
MACHINE, FILEPATH, FILENAME, HASH, URL**. Each entity is emitted twice -

    ipv4 ipv4_46_244_86_46

the bare label, so term frequency carries "this alert mentions four addresses",
and `label_value` with separators collapsed, because TF-IDF's default `\w\w+`
token pattern would cut `46.244.86.46` down to `46` and throw the rest away.

### USERNAME is excluded

USERNAME was the highest-volume label - 84,339 alert-occurrences at >= 0.999,
more than the other eight together - and its four commonest values were `user`,
`medium`, `high` and `low`.

Those are not ordinary NER errors. The notable is rendered as one `key: value`
line per field, so the model reads

    severity: high
    urgency: medium
    user_risk_object_type: user

and tags the value of a *metadata* field as a person. On its own training
distribution this model scores 0.986 precision; the failure is entirely one of
input distribution.

`ner/username_context.py` measures it. Every span's character offset identifies
the line it sits on, so the field it came from is recoverable, and a token-based
rule separates fields that name an account (`destinationUserName`, `accountName`,
`_risk_user`) from fields that describe one (`user_priority`, `user_category`,
`user_risk_object_type`). The verdict: **82.7% of high-confidence USERNAME spans
were metadata values, not accounts.**

Three policies, out-of-fold over five expanding forward folds, three seeds:

| USERNAME policy | ROC | PR | Alerts with >= 1 |
| --- | ---: | ---: | ---: |
| keep every span | 0.8297 +-0.0002 | 0.6523 | 87.1% |
| keep only account fields | 0.8293 +-0.0001 | 0.6523 | 34.5% |
| **drop the label** (shipped) | **0.8287 +-0.0001** | 0.6519 | — |
| no entities at all | 0.8239 +-0.0001 | 0.6376 | — |

Removing 82.7% of the spans moved ROC by 0.0004, and the whole label is worth
0.0010 against the other eight labels' 0.0048. It buys nothing measurable, so it
is dropped rather than patched - a 35-key hand-curated allowlist is a maintenance
burden that would rot as new log sources arrive. `dataset_v10.py` still carries
all three policies as columns (`USERNAME_MODES`) so the measurement can be redone
without another GPU hour.

### What it costs

| Release | Input | ROC | PR | Terms |
| --- | --- | ---: | ---: | ---: |
| `ml/v9` | whole notable | **0.8523** | 0.6888 | 168,679 |
| `ml/v10` | rule fields + 8 entity types | 0.8285 | 0.6516 | ~31,000 |

**0.0238 ROC** for a model that reads five named fields and eight entity types
instead of 1,398 arbitrary keys. Every weight traces to something you can point
at. Whether that trade is worth making is a deployment decision, not a modelling
one.
