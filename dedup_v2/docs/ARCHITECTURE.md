# STIX Alert Deduplication Engine v2.0 — Architecture

## Overview

Deduplication engine for STIX 2.1 security alerts. Two-layer dedup with cross-validation scoring. Built with FastAPI and Redis.

---

## Project Structure

```
dedup_v2/
├── app/
│   ├── main.py                  # FastAPI app, lifespan (Redis pool), CORS
│   ├── config.py                # Loads config.yaml into Settings class
│   ├── models.py                # Pydantic request/response schemas
│   ├── logger.py                # Structured JSONL audit + error logger
│   ├── routers/
│   │   ├── alerts.py            # POST /alerts, GET /alerts/{id}
│   │   ├── admin.py             # DELETE /admin/flush
│   │   ├── dashboard.py         # GET /dashboard, GET /dashboard/data
│   │   └── health.py            # GET /health
│   ├── services/
│   │   ├── redis_client.py      # Async Redis (forward index, inverted index, bundle store)
│   │   ├── stix_parser.py       # STIX 2.1 bundle → flat alert dict
│   │   ├── ioc_normalizer.py    # IOC normalization
│   │   ├── fingerprinter.py     # Per-slot SHA-256 hashing + time bucket
│   │   ├── layer1.py            # Slot-by-slot comparison + confidence scoring
│   │   ├── layer2.py            # Fuzzy matching (Token Sort Ratio)
│   │   ├── stix_equivalence.py  # STIX Object + Observable similarity
│   │   └── dedup_engine.py      # Orchestrates the full dedup pipeline
│   └── templates/
│       └── dashboard.html       # Dashboard UI (Chart.js)
├── stream_alerts.py             # Streams CSV alerts to the API (with pre/post Redis flush)
├── config.yaml                  # All configuration
├── data/
│   └── dedup_input_test.csv     # Test alert data
├── logs/
│   ├── dedup.jsonl              # Structured audit log
│   └── error.jsonl              # Error log
├── docker-compose.yml           # Redis container
└── requirements.txt
```

---

## Pipeline Phases

### 1. Extraction

**Module:** `app/services/stix_parser.py`

Parses the incoming STIX 2.1 bundle into a flat alert dict. IOCs are extracted from two sources:

**Standard STIX objects (universal):**

| STIX Object Type | IOC Type | Field |
|------------------|----------|-------|
| `ipv4-addr` | ip | `value` |
| `ipv6-addr` | ip | `value` |
| `domain-name` | domain | `value` |
| `url` | url | `value` |
| `file` | file_hash | `hashes` (algorithms from config) |
| `user-account` | user | `user_id`, `account_login` |

**Splunk notable fields (vendor extension, fallback):**

Extracted from `indicator.extensions[extension_key].vendor_specific.notable_fields`. Field mappings defined in `config.yaml` under `stix.notable_field_mappings`. This covers vendor-specific field names like `ipAddress`, `destinationUserName`, `accountName`, etc.

143 of 422 production alerts (34%) have IOCs **only** in the vendor extension. Without notable field extraction, those alerts would have zero IOCs.

---

### 2. Normalization

**Module:** `app/services/ioc_normalizer.py`

Before comparison, all IOC values are normalized for consistency:

| IOC Type | Normalization |
|----------|---------------|
| ip (v4) | Strip CIDR, remove leading zeros per octet |
| ip (v6) | Expand `::` to full 8-group hex |
| domain | Lowercase, strip trailing FQDN dot |
| url | Lowercase scheme+host, remove default ports |
| email | Lowercase |
| file_hash | Lowercase |
| user | Lowercase |

No blocklist filtering. Every extracted IOC goes into the fingerprint.

---

### 3. Fingerprinting

**Module:** `app/services/fingerprinter.py`

Each IOC slot is hashed individually as `SHA-256("{slot_type}:{sorted_values}")`. The fingerprinter returns two separate dicts:

- **Prerequisites:** `alert_name` and `time_bucket` hashes (gate for Layer 1)
- **IOC hashes:** the 6 IOC slots (scored for confidence)

**Time bucket** is based on **ingestion time** (when the engine receives the alert):

```
time_bucket = floor(ingestion_epoch_ms / (window_minutes * 60 * 1000))
```

---

### 4. Input Validation

If the parser extracts **zero IOCs** from a Splunk/LSU alert:
- This is a **data quality issue** — we know the structure, IOCs should be present
- Log it as a data quality warning
- Return **NEW**
- Do not proceed to lookup

---

### 5. Layer 1 — Hash Dedup

**Module:** `app/services/layer1.py`

Detects exact duplicate alerts — same rule, same IOCs. Uses a two-stage approach: prerequisites gate the comparison, IOCs drive the score.

| Stage | Slots | Purpose | Method |
|-------|-------|---------|--------|
| **Stage 1: Prerequisites** | `alert_name`, `time_bucket` | Quick screen — different rule or different time window = not a duplicate | Redis `SINTER` — both must match |
| **Stage 2: IOC Confidence** | `ip`, `user`, `email`, `domain`, `url`, `file_hash` | Confidence scoring — how similar are the observables | Count matches, calculate ratio |

#### Stage 1 — Prerequisites (gate)

Query the inverted index for `alert_name` and `time_bucket` hashes. Use Redis `SINTER` to find candidates that match **both**.

```
{prefix}:l1:idx:alert_name:hash_aaa  → {alert_1, alert_5, alert_7, alert_12}
{prefix}:l1:idx:time_bucket:hash_bbb → {alert_1, alert_5, alert_7}

SINTER → {alert_1, alert_5, alert_7}
```

Only these 3 candidates have the same rule AND same time window. All others eliminated without checking IOCs. If `SINTER` returns empty → **NEW**.

#### Stage 2 — IOC Comparison (score)

Among prerequisite-matched candidates, query the **inverted index** for each filled IOC slot. Each slot hash is stored in a Redis set keyed by `{slot_type}:{slot_hash}` — same pattern as Elasticsearch/Lucene.

```
{prefix}:l1:idx:ip:hash_ccc    → {alert_1, alert_5}
{prefix}:l1:idx:user:hash_ddd  → {alert_1, alert_5}
{prefix}:l1:idx:email:hash_eee → {alert_1, alert_5, alert_7}

Counting (only prerequisite-matched candidates):
  alert_1: 3 of 3 IOC sets → 3/3 = 100%
  alert_5: 3 of 3 IOC sets → 3/3 = 100%
  alert_7: 1 of 3 IOC sets → 1/3 = 33%
```

#### Hash Confidence

```
confidence = matched_ioc_slots / total_filled_ioc_slots
```

Only the **6 IOC slots** count — prerequisites are excluded. They are the gate, not the score.

| Matched | Total | Confidence |
|---------|-------|------------|
| 6 | 6 | 100% |
| 5 | 6 | 83.3% |
| 4 | 6 | 66.7% |
| 3 | 6 | 50% |
| 2 | 6 | 33.3% |
| 1 | 6 | 16.7% |

If confidence ≥ `layer1.confidence_threshold` → **DUPLICATE**. Otherwise → pass to Layer 2.

**Earliest parent wins:** When multiple candidates have the same confidence, the engine picks the one that arrived earliest (by ingestion time). All duplicates point to the original parent, not to each other.

**Full match example (100%):**
```
Prerequisites: alert_name ✓, time_bucket ✓ (passed Stage 1)

IOC comparison:
  ip:      hash_ccc  ✓ match
  user:    hash_ddd  ✓ match
  email:   hash_eee  ✓ match

→ 3/3 = 100% confidence → DUPLICATE
```

**Partial match example (67%):**
```
Prerequisites: alert_name ✓, time_bucket ✓ (passed Stage 1)

IOC comparison:
  ip:      hash_ccc  ✓ match
  user:    hash_ddd  ✓ match
  email:   hash_eee  ✗ no match

→ 2/3 = 66.7% confidence
```

#### Slots

**Prerequisites (Stage 1 — gate):**

| Slot | Always Present | Purpose |
|------|---------------|---------|
| `alert_name` | Yes (100%) | Same detection rule |
| `time_bucket` | Yes (100%) | Same time window |

**IOC Slots (Stage 2 — confidence):**

| Slot | Present in LSU data | Example |
|------|-------------------|---------|
| `ip` | 75% | `203.0.113.45` |
| `user` | 63% | `jdoe@acme.com` |
| `email` | 60% | `jdoe@acme.com` |
| `domain` | 12% | `mail.acme.com` |
| `url` | 11% | `https://portal.acme.com/login` |
| `file_hash` | 3% | `e3b0c44298fc1c14...` |

The typical LSU/Splunk alert has **3 filled IOC slots**: ip, user, email.

#### Storage

One Redis key per alert:

```
Key:   {prefix}:{customer_id}:l1:{alert_id}
Value: {
  "alert_id": "...",
  "timestamp": "...",     ← ingestion time (for earliest-parent logic)
  "hashes": {
    "alert_name": "a1b2c3...",
    "time_bucket": "d4e5f6...",
    "ip": "g7h8i9...",
    "user": "j0k1l2...",
    "email": "m3n4o5..."
  }
}
```

#### Edge Cases

**Duplicate parent chain:** All alerts (including duplicates) are stored in the inverted index. Without handling, duplicates could chain to other duplicates. Solution: earliest parent wins on tie. All keys expire after `redis.ttl_minutes` (30 minutes), so the index cleans itself regardless.

**Time bucket boundary:** Two identical alerts crossing a bucket boundary (12:29 and 12:31) won't match at Stage 1. These cases are deferred to Layer 2.

---

### 6. Layer 2 — Fuzzy Matching

**Module:** `app/services/layer2.py`

Detects similar alerts across **different detection rules** within the same time window. Only runs if Layer 1 returned NEW.

#### Prerequisites

Same `time_bucket` only. `alert_name` must be **different** — same rule alerts were already handled by Layer 1. No overlap between the two layers.

```
Layer 1: same alert_name + same time_bucket → exact hash match
Layer 2: different alert_name + same time_bucket → fuzzy value match
```

#### Token Sort Ratio

Compares IOC values using Token Sort Ratio (from rapidfuzz library). Tokenizes both strings, sorts alphabetically, computes character-level match ratio (0-100).

| Value A | Value B | Score | Why |
|---------|---------|-------|-----|
| `jreaga3@lsu.edu` | `jreaga3@lsu.edu` | 100 | Identical |
| `myekrangian` | `jreaga3` | ~44 | Low overlap |
| `myekrangian@lsu.edu` | `jreaga3@lsu.edu` | ~65 | Shared @lsu.edu |
| `6.244.192.143` | `6.244.192.143` | 100 | Identical IP |

#### Per-Type and Overall Scoring

For each IOC type present in either alert:
1. For each incoming value, find the best fuzzy match among stored values
2. Type score = average of best matches

Overall fuzzy score = average across all IOC types.

If fuzzy score ≥ `layer2.similarity_threshold` → **SIMILAR**.

#### Test Cases

**test6 — Same IOCs, different rule (fuzzy: 100):**
```
Rule A: Defender - New High Severity Alerts     → user: jreaga3@lsu.edu, ip: 6.244.192.143
Rule B: Azure Audit - Failed Logins (Outside USA) → user: jreaga3@lsu.edu, ip: 6.244.192.143

Fuzzy: ip=100, user=100, email=100 → Overall: 100 → SIMILAR
```

**test7 — Different IOCs, different rule (fuzzy: 58):**

| Field | test3 (parent) | test7 (incoming) |
|-------|---------------|-----------------|
| Rule | Defender - New High Severity Alerts | Windows - Basic Brute Force Detection |
| User | jreaga3@lsu.edu | myekrangian |
| Email | jreaga3@lsu.edu | myekrangian@lsu.edu |
| IP | 6.244.192.143 | 6.244.192.143 |

```
Fuzzy: ip=100, user=44, email=65 → Overall: 69.8
Result depends on threshold:
  Threshold 60 → SIMILAR
  Threshold 80 → NEW
```

#### Layer 2 Scores

SIMILAR alerts get five scores:

| Score | What it measures |
|-------|-----------------|
| **Fuzzy score** | Layer 2's own score — average fuzzy match across IOC types |
| **Hash confidence** | Set to 0 — Layer 2 doesn't do hash matching |
| **STIX Object** | Context similarity (indicator, identity, attack-pattern) |
| **Observable/Type** | Per-type exact value match (same data as hash) |
| **Observable/Value** | Per-value exact match across all types |

The fuzzy score and Observable scores measure different things:
- **Fuzzy** uses Token Sort Ratio — `myekrangian` vs `jreaga3` = 44% (partial string similarity)
- **Observable** uses exact set intersection — `myekrangian` vs `jreaga3` = 0% (no exact match)

#### What Layer 2 catches

- Different rules firing on the same user/IP
- Same attack reported by different detection rules
- Cross-rule correlation within the same time window

#### What Layer 2 doesn't catch

- Similar alerts in different time buckets (same boundary limitation as Layer 1)
- Alerts with no IOC overlap at all

---

### 7. Cross-Validation

**Module:** `app/services/stix_equivalence.py`

Runs for both DUPLICATE and SIMILAR verdicts. Provides independent scores to cross-validate the primary dedup decision. Three scores from two perspectives.

#### STIX Object Similarity

Uses the `stix2` library's `object_similarity()` to compare **threat context** objects. Score 0-100.

| Object Type | Properties Compared | Weights (library defaults) |
|-------------|--------------------|----|
| `indicator` | pattern, indicator_types, valid_from | 80, 15, 5 |
| `identity` | name, identity_class, sectors | 60, 20, 20 |
| `attack-pattern` | name, external_references | 30, 70 |

Measures: same rule? same org? same MITRE techniques? Independent of IOC values — a different user at the same org with the same rule still scores 100.

The weights are built into the stix2 library — not configured by us.

Reference: [STIX 2 Object Equivalence](https://stix2.readthedocs.io/en/latest/guide/equivalence.html)

#### Observable by Type

For each IOC type present in either alert:

```
type_score = |intersection| / |union| × 100
```

Overall = average across all IOC types.

Uses **parsed IOC data** (same as hash confidence) — no gap from vendor notable fields. This ensures hash confidence and Observable/Type correlate: both look at the same data, just from different angles (hash vs exact value comparison).

**Example — test5 (different user, same URL):**
```
url:   incoming={'portal.acme.com/login'}  stored={'portal.acme.com/login'}  → 1/1 = 100%
user:  incoming={'jdoe@acme.com'}          stored={'abirds8@acme.com'}       → 0/2 = 0%
email: incoming={'jdoe@acme.com'}          stored={'abirds8@acme.com'}       → 0/2 = 0%

Overall: (100 + 0 + 0) / 3 = 33.3%
```

#### Observable by Value

Pool **all** IOC values across **all** types into one set:

```
score = total_matched_values / total_unique_values × 100
```

Gives the true data overlap — a type with 10 matched IPs counts more than a type with 1 user. Per-type averaging (Obs/Type) treats them equally — Obs/Value does not.

**Example — test5 (different user, same URL):**
```
All incoming values: {'portal.acme.com/login', 'jdoe@acme.com', 'jdoe@acme.com'}
All stored values:   {'portal.acme.com/login', 'abirds8@acme.com', 'abirds8@acme.com'}
Union: 5 unique values, Matched: 1 (the URL)

Obs/Value: 1/5 = 20%
Obs/Type:  33%  (averages per type — url=100, user=0, email=0 → 33)
```

#### Score Correlation

| Pattern | Hash | STIX Obj | Obs/Type | Obs/Value | Meaning |
|---------|------|----------|----------|-----------|---------|
| Exact duplicate | 100% | 100 | 100 | 100 | Same everything |
| Same rule, different target | 33% | 100 | 33 | 20 | Same threat, different user |
| Cross-rule, same IOCs | — | 100 | 100 | 100 | Different rules, same attack |
| Cross-rule, partial IOCs | — | 100 | 44 | 33 | Different rules, partial overlap |

Disagreement between scores is the signal: high Object + low Observable = same attack, different target.

---

### 8. Results

Three possible verdicts:

#### DUPLICATE (Layer 1)

Same rule, exact IOC match above hash confidence threshold.

**Scores:** hash_confidence, STIX Object, Observable/Type, Observable/Value

```
Hash:       100%  — all IOC slots matched
STIX Obj:   100   — same rule, org, techniques
Obs/Type:   100   — all types matched
Obs/Value:  100   — all values matched
```

#### SIMILAR (Layer 2)

Different rule, fuzzy IOC match above similarity threshold.

**Scores:** fuzzy_score, STIX Object, Observable/Type, Observable/Value

```
Fuzzy:      70    — IP exact, user/email partial
STIX Obj:   100   — same org, same techniques
Obs/Type:   44    — only IP matched exactly
Obs/Value:  33    — 1 value matched out of 3 unique
```

#### NEW

No match in either layer. Alert is new — not a duplicate, not similar to anything in the time window.

---

### 9. Audit Logs

Two log files in `logs/`:

#### dedup.jsonl — Audit trail

One JSON line per alert. Every entry includes:

**Result:** verdict, layer, hash_confidence, stix_context_similarity, stix_observable_by_type, stix_observable_by_value, matched_alert_id, filled_slots, matched_slots, missed_slots

**Layer 2 specific:** fuzzy_score, per_type_scores, incoming_rule, matched_rule

**STIX details:** stix_context_details (per-object scores), stix_observable_details (per-IOC-type scores with incoming/stored/matched values)

**Input data:** input_iocs, input_alert, input_hashes, candidate_counts, time_bucket

#### error.jsonl — Error log

One JSON line per error. Logged when any stage fails:

**Fields:** event, alert_id, customer_id, stage, error_type, error_message, traceback, input_data

**Stages covered:** parse, extract_iocs, fingerprint, layer1_lookup, layer2_fuzzy, stix_equivalence_l1, stix_equivalence_l2, redis_store, audit_log, api_process_alert

Errors are caught and logged — the engine never crashes. Failed alerts pass through as NEW with error details in the score_breakdown.

---

## Error Handling

Every stage of the pipeline is wrapped in try/except. Errors are:
1. Logged to `logs/error.jsonl` with full context (alert_id, stage, traceback, input data)
2. The alert returns as **NEW** with error info in `score_breakdown`
3. The API never returns a 500 for processing errors — the alert is handled gracefully

---

## Configuration (config.yaml)

```yaml
redis:
  url: "redis://localhost:6379/0"
  key_prefix: "dedup"
  ttl_minutes: 30

logging:
  max_bytes: 10485760
  backup_count: 5

stix:
  extension_key: "extension-definition--a0b1c2d3-..."
  hash_algorithms: ["SHA-256", "SHA-1", "MD5"]
  notable_field_mappings:
    ip: [IOC, ipAddress, sourceAddress, src_ip, ...]
    user: [user, duser, accountName, ...]
    email: [destinationUserName, userPrincipalName, ...]
    domain: [deviceHostname, deviceDnsName, ...]
    url: [url, requestURL]
    file_hash: [sha256, fileHashSha256]
  ioc_field_map:
    ips: ip
    domains: domain
    urls: url
    file_hashes: file_hash
    emails: email
    usernames: user

dedup:
  window_minutes: 30
  layer1:
    slots: [ip, user, email, domain, url, file_hash, alert_name, time_bucket]
    confidence_threshold: 0.10
    confidence_buckets: ["0.0", "16.7", "33.3", "50.0", "66.7", "83.3", "100.0"]
    stix_equivalence:
      enabled: true
      threshold: 10
  layer2:
    similarity_threshold: 10
```

---

## Redis Key Design

```
{prefix}:{customer_id}:l1:{alert_id}                    → Forward index (hashes + timestamp)
{prefix}:{customer_id}:l1:idx:{slot_type}:{slot_hash}   → Inverted index (set of alert_ids)
{prefix}:{customer_id}:l1:bundle:{alert_id}             → Raw STIX bundle + parsed IOCs + alert_name
{prefix}:{customer_id}:result:{alert_id}                 → Cached dedup result
```

All keys TTL = `redis.ttl_minutes` (30 minutes).

---

## API Endpoints

| Method | Path | Description | Response |
|--------|------|-------------|----------|
| `POST` | `/api/v1/alerts` | Process single STIX bundle | `AlertOut` |
| `GET` | `/api/v1/alerts/{alert_id}` | Retrieve cached dedup result | `AlertOut` |
| `DELETE` | `/api/v1/admin/flush` | Flush all Redis data | `{status}` |
| `GET` | `/api/v1/dashboard/data` | Aggregated log analytics | JSON |
| `GET` | `/dashboard` | Dashboard UI | HTML |
| `GET` | `/api/v1/health` | Liveness + Redis check | `HealthResponse` |

---

## Quick Start

```bash
docker compose up -d
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
python stream_alerts.py
open http://localhost:8042/dashboard
```

---

## Open Questions

- **Confidence threshold**: Layer 1 hash confidence threshold currently `0.10` for testing. Needs validation with real production data.
- **Fuzzy threshold**: Layer 2 similarity threshold currently `0.10` for testing. Needs tuning.
- **Queue/buffer**: Depending on alert volume and burst patterns, a queue in front of the API may be needed. To be followed up.

---

## Full Configuration Reference

| Config Key | Purpose |
|------------|---------|
| `redis.url` | Redis connection string |
| `redis.key_prefix` | Redis key namespace |
| `redis.ttl_minutes` | How long keys live in Redis |
| `logging.max_bytes` | Log rotation size |
| `logging.backup_count` | Number of rotated log files |
| `stix.extension_key` | Vendor extension UUID for notable fields |
| `stix.hash_algorithms` | File hash algorithms to extract |
| `stix.notable_field_mappings` | Splunk field names → IOC types |
| `stix.ioc_field_map` | Alert dict fields → IOC types |
| `dedup.window_minutes` | Time bucket size |
| `dedup.layer1.slots` | All 8 slot types (prerequisites + IOCs) |
| `dedup.layer1.confidence_threshold` | Minimum IOC confidence for DUPLICATE |
| `dedup.layer1.confidence_buckets` | Dashboard confidence distribution buckets |
| `dedup.layer1.stix_equivalence.enabled` | Enable/disable STIX cross-validation |
| `dedup.layer1.stix_equivalence.threshold` | Minimum STIX similarity score |
| `dedup.layer2.similarity_threshold` | Minimum fuzzy score for SIMILAR |

---

## References

- [STIX 2 Object Equivalence](https://stix2.readthedocs.io/en/latest/guide/equivalence.html) — stix2 library's `object_similarity()` used for STIX Object cross-validation
- [DEDUP_ENGINE_DIAGRAM.md](DEDUP_ENGINE_DIAGRAM.md) — Full architecture diagram (Mermaid)
