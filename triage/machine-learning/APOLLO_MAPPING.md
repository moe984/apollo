# What Apollo sends

The model is **trained** on the SOAR export and **served** by Apollo. This records
what Apollo actually forwards, measured by comparing values rather than field
names.

- Training source: `soar-containers-12mo-20260923.jsonl.gz` — 123,897 containers,
  32,429 labelled, 30,830 with a readable ES notable.
- Inference source: `apollo-soar-alerts-20260923.jsonl.gz` — 13,339 records,
  13,145 distinct containers, all on `prod-soar2`.

## The headline

**Apollo forwards the ES notable itself**, in a `soar_cef` array — not a summary
of it. On 13,316 of 13,339 records (99.8%), `soar_cef` holds exactly one artifact
and that artifact passes `is_notable()`.

Once two keys are excluded, the string built from Apollo is **byte-identical** to
the string built from the SOAR export, on all 7,032 validated joins.

```
scored from the SOAR-built string     ROC 0.8215
scored from the ACTUAL Apollo record  ROC 0.8215
max score difference                  0.00e+00
```

> An earlier version of this document concluded that 91.4% of the training input
> was missing from Apollo. That was wrong: it examined only the `result` payload
> and never scanned the rest of the record. `soar_cef` was there the whole time.

## The two asymmetric keys

| Key | Present | Effect if kept |
| --- | --- | --- |
| `extract_artifacts` | SOAR only — 100% of joins | a near-constant Splunk config blob, 0.89% of training terms |
| `splunk_query` | Apollo only — 46% of records | nothing to train on |

Left in, the same alert scores differently depending on which side built the
string: mean 0.042, max 0.38, against a zero-miss threshold of 0.0014. Both are
in `dataset_v11.DROP`, and with them gone the difference is exactly zero. Removing
them costs 0.0000 ROC.

Of the 376 training keys absent from Apollo's sample, only 20 occur on
prod-soar2 inside Apollo's own window, and 19 of those appear on 8–9 alerts each.
`extract_artifacts` is the rest. The other 356 are missing because the sample is
one server over two months, not because the feed drops them.

## Validating a join

`(server, id)` is unique in the SOAR export — 123,897 ids for 123,897 records — so
`prod-soar2:<container_id>` looks like a clean key, and 88.2% of Apollo's ids
exist there.

**But the id spaces only converged recently.** Requiring `source_event_id` to
agree on both sides:

| Container created | Joined | Join actually valid |
| --- | ---: | ---: |
| 2026-07 | 3,434 | **0.4%** |
| 2026-08 | 5,316 | 79.0% |
| 2026-09 | 2,841 | **100%** |

A July container id points at a *different alert* in each system. **Any analysis
joining these two sources must validate on `source_event_id`** or it will
silently compare unrelated alerts.

This does not affect serving — Apollo carries its own notable, so no join is
needed at inference. It matters only for offline evaluation.

## The `result` payload

Apollo also carries a `result` object with 14 pre-parsed fields. The model does
not use it; `soar_cef` already has everything. For reference, each field and the
notable field holding the same value, over validated joins:

| Apollo `result` field | Present | → notable field | Match |
| --- | ---: | --- | ---: |
| `search_name` | 100% | `rule_name` | 100% |
| `severity` | 99.9% | `severity`, `severities` | 85.7% |
| `user` | 91.9% | `destinationUserName` | 97.6% |
| `src_ip` | 46.3% | `sourceAddress` | 98.7% |
| `incidentId` | 41.8% | `incidentId` | 100% |
| `dest_host` | 23.6% | `destinationHostName` | 100% |
| `src_user` | 17.4% | `sourceUserName` | 100% |
| `userPrincipalName` | 15.5% | `userPrincipalName` | 100% |
| `dest_ip` | 10.7% | `destinationAddress` | 100% |
| `file_hash` | 6.6% | `fileHashSha256` | 100% |
| `src_host` | 4.9% | `sourceHostName` | 86.4% |
| `url` | 1.1% | `requestURL` | 100% |
| `source_event_id` | 100% | `rule_id`, `event_id` | 100% — but dropped as plumbing |
| `container_id` | 100% | *(nothing)* | — |

Note the names differ while the values match: Apollo's `user` is the notable's
`destinationUserName`. Since the model fits `key value` pairs, those would be
unrelated terms — which is why the model reads `soar_cef` and ignores `result`
entirely.

`result` is also lossy: for `prod-soar2:242783` the notable carries eleven
`destinationUserName` values and `result.user` carries one.

## Serving

```python
cef = apollo_record.get("soar_cef") or []
if not cef:                      # 0.17% of records — no notable at all
    route_to_human(reason="no ES notable in payload")
else:
    text = dataset_v11.to_text(cef[0])
    p    = model.predict_proba(pd.DataFrame({"text_cef": [text]}))[:, 1]
```

Apollo sends only the arrival artifact — no settings/metrics, no `soar_event` —
so there is no label-leakage exposure at serving time.
