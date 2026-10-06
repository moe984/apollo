# Cortex XSIAM/XSOAR Alert Deduplication — Competitive Review

**Date:** 2026-05-14
**Author:** CIP Platform Engineering
**Purpose:** Document how Palo Alto's Cortex XSIAM and XSOAR handle alert deduplication, compare against our Apollo dedup pipeline, and identify gaps and opportunities.

---

## 1. How Cortex Does Dedup

Palo Alto uses **two separate dedup systems** depending on the product:

### 1.1 XSIAM: Out-of-the-Box (OOTB) Dedup

XSIAM provides built-in, automatic alert deduplication as a platform feature. Key characteristics:

- **Fingerprint-based**: Generates a deterministic hash from selected alert fields
- **Dedup key**: A single composite key compared via **exact equality** against previous alerts
- **Time-windowed**: Configurable dedup window per alert type. If an alert with the same key arrives while a previous one is still active or within the window, it is suppressed
- **Per-alert-type rules**: Different alert types can have different key fields and different time windows
- **No similarity scoring**: It's binary — the fingerprint matches or it doesn't
- **Sliding window option**: The window can reset with each new occurrence (sliding) or be fixed

#### Fingerprint Construction

The fingerprint is a SHA-256 hash generated from selected fields, sorted alphabetically and converted to JSON:

- **Good fields** (identify root cause): `alertname`, `host`, `service`
- **Bad fields** (too specific): `timestamp`, `value` (these change every time)
- **Bad fields** (too broad): only `alertname` (merges unrelated alerts)

#### Normalization Before Hashing

Cortex applies basic normalization before fingerprinting:

- **Hostnames**: Strip domain suffixes (`db-server-01.prod.example.com` → `db-server-01`)
- **Error messages**: Replace numbers and UUIDs with placeholders (`Connection timeout after 30.5s` → `Connection timeout after Xs`)

This prevents minor field variations from breaking the fingerprint match.

#### Time Window Configuration

Default windows vary by alert type:

| Alert Type | Window | Rationale |
|------------|--------|-----------|
| Network alerts | 60 seconds | Transient, fast-resolving issues |
| Database alerts | 900 seconds (15 min) | Persistent, slow-resolving issues |
| Default | 300 seconds (5 min) | General-purpose |

**Fixed vs Sliding**:
- **Fixed window**: Alert group is active for a set duration from first occurrence
- **Sliding window**: Window resets with each new alert occurrence — the group stays active as long as duplicates keep arriving

#### What Happens to Duplicates

- Duplicate alert is **not created** as a new incident
- The count on the existing alert group is incremented
- The `last_seen` timestamp is updated
- The sample alert data may be updated

#### Escalating Notification Thresholds

Instead of suppressing all duplicates silently, XSIAM re-notifies at escalating counts:

| Count | Action |
|-------|--------|
| 1 | Always notify (first occurrence) |
| 5 | Re-notify (problem is recurring) |
| 10 | Re-notify |
| 50 | Re-notify (significant volume) |
| 100 | Re-notify |
| 500 | Re-notify |
| 1000 | Re-notify (critical volume) |

This prevents transient noise while surfacing patterns of worsening issues.

---

### 1.2 XSOAR: Playbook-Based Dedup (Generic v3/v4)

XSOAR uses playbook-driven dedup with more intelligence but more configuration overhead.

#### Three Matching Methods

| Method | How It Works | Best For |
|--------|-------------|----------|
| **ML** | Machine learning model, trained mostly on phishing incidents | Phishing, BEC |
| **Rules** | Logic-based matching on labels and custom fields | Well-defined alert types with known key fields |
| **Text** | Statistical text comparison algorithm | General-purpose, especially description-heavy alerts |

#### Configuration Parameters

| Parameter | Purpose | Default |
|-----------|---------|---------|
| `DuplicateThreshold` | Similarity score range (0.0 - 1.0) | 0.70 |
| `TimeFrameHours` | Lookback window for candidate search | 72 hours |
| `IgnoreClosedIncidents` | Whether to skip already-closed incidents | yes |
| `MaxNumberOfCandidates` | Maximum candidates to compare against | 1000 |
| `CloseAsDuplicate` | Automatically close the duplicate | true |
| `TimeField` | Which timestamp to use (created/occurred/modified) | created |
| `similarLabelsKeys` | Custom label keys for matching (supports substring and word-count variance) | — |
| `similarIncidentFields` | Fields to compare (labels, incident fields, custom fields) | `name, type, details` |

#### Action on Match

When a duplicate is found:
1. The current incident is **closed**
2. It is **linked** to the older incident (maintaining audit trail)
3. The relationship is recorded for investigation context

#### Dependencies

The playbook relies on these scripts:
- `FindSimilarIncidents` — searches for candidates
- `PhishingDedupPreprocessingRule` — phishing-specific preprocessing
- `CloseInvestigationAsDuplicate` — closes and labels the duplicate
- `FindSimilarIncidentsByText` — text-based similarity search
- `linkIncidents` — creates the parent-child link

#### Storage

- **Single instance**: In-memory dictionary with thread locking
- **Distributed**: Redis with Lua scripts for atomic check-and-update, TTL-based cleanup

---

## 2. Side-by-Side Comparison: Cortex vs Apollo

| Aspect | Cortex XSIAM/XSOAR | Apollo Dedup Pipeline |
|--------|--------------------|-----------------------|
| **Architecture** | Single fingerprint hash (XSIAM) OR ML/text similarity (XSOAR) | Three-layer cascade: hash → IOC similarity → rule match |
| **Matching method** | Exact key match (XSIAM) or threshold-based similarity (XSOAR) | Exact hash, weighted Jaccard, rule-name match |
| **Fingerprint fields** | Configurable per alert type (`alertname`, `host`, `service`) | Fixed: `srcIp \| destIps+domains \| alertCategory \| timeBucket` |
| **Similarity scoring** | Threshold 0-1 (default 0.70) for ML/text mode | Threshold 0-1 (0.75 review, 0.90 suppress) for IOC scoring |
| **IOC awareness** | None — treats alerts as generic field bags | IOC-type-aware with per-type weights (file_hash=1.0, ip=0.3) |
| **Temporal handling** | Binary: within window or not (fixed or sliding) | Graduated 6-bracket decay: 1.0→0.8→0.6→0.4→0.2→0.1 over 24h |
| **Time window defaults** | 60s-900s per alert type (default 300s) | 30 minutes single window |
| **Per-rule configuration** | Yes — different fields/windows per alert type | No — same logic for all alerts |
| **Cross-vendor dedup** | Not explicitly supported — fingerprint is per-source | Layer 2 catches cross-vendor via shared IOCs |
| **Action on duplicate** | Close + link to parent, increment count | SUPPRESS or REVIEW verdict |
| **ML support** | Yes (trained on phishing incidents) | No |
| **Text similarity** | Yes (statistical text comparison) | No |
| **IOC normalization** | Basic (strip domain suffixes, replace numbers) | Full canonicalization (IP, domain, URL, email, hash) + block-list |
| **Field weighting** | No — all fields contribute equally to fingerprint | Yes — file_hash (1.0) counts 3.3x more than ip (0.3) |
| **Count escalation** | Yes — re-notify at 5, 10, 50, 100, 500, 1000 | No notification/escalation logic |
| **Storage** | Redis (distributed) or in-memory | In-memory only (POC) |
| **Block-list filtering** | Not mentioned | Private IPs, public DNS, common domains filtered |
| **Fallback layers** | No cascade — single method per configuration | L1→L2→L3 cascade with short-circuit on first match |

---

## 3. What Cortex Does That We Don't (Gaps)

### 3.1 Per-Alert-Type Dedup Rules

**Gap**: Cortex allows defining different key fields and time windows per alert type. Apollo uses the same dedup logic for all alerts.

**Impact**: This is the root cause of our L3 over-matching problem. The "Defender - New High Severity Alerts" rule (62 alerts with different IPs and users) gets grouped together just because the rule name matches. A per-rule config would let us specify that this rule should dedup on `ip + user`, not just rule name.

**Example of what Cortex supports**:
```
Account Lockout:
  key_fields: [alertname, username]
  window: 300s

Defender High Severity:
  key_fields: [alertname, src_ip, user]
  window: 900s

Brute Force:
  key_fields: [alertname, src_ip, dest_ip]
  window: 600s
```

### 3.2 Sliding Time Windows

**Gap**: Our Layer 1 hash dedup uses fixed time buckets (`floor(timestamp / window_ms)`). Two alerts 1 second apart can land in different buckets if they straddle a boundary.

**Impact**: Potential false negatives at bucket boundaries. Cortex's sliding window (reset on each new occurrence) avoids this entirely.

### 3.3 Escalating Notification Thresholds

**Gap**: We have SUPPRESS (silent) and REVIEW (analyst queue) verdicts, but no concept of "re-notify after N suppressed duplicates."

**Impact**: If a brute-force attack generates 500 duplicate alerts, we suppress 499 silently. An analyst never sees that the volume is escalating. Cortex would re-notify at 5, 10, 50, etc.

### 3.4 Duplicate Linking and Count Tracking

**Gap**: We output a `matched_alert_id` in CSV but don't formally link duplicates to their canonical alert or maintain a running count.

**Impact**: In production, the parent alert should accumulate context from its duplicates — how many there are, what time range they span, whether they're escalating.

### 3.5 ML-Based Similarity

**Gap**: Cortex XSOAR offers ML-trained dedup (especially for phishing). We have no ML component.

**Impact**: Low priority for now — our IOC-based scoring covers most cases. ML would help for description-heavy alerts where IOCs are sparse (phishing emails, BEC attempts).

### 3.6 Text-Based Similarity

**Gap**: Cortex XSOAR has a statistical text comparison mode for alert descriptions. We don't compare description or title text.

**Impact**: Could catch duplicates where IOCs differ but the human-readable description is nearly identical. Lower priority than per-rule config.

---

## 4. What We Do That Cortex Doesn't (Advantages)

### 4.1 IOC-Aware Weighted Scoring

Cortex treats all fingerprint fields equally. Our Layer 2 weights IOC types by importance:

| IOC Type | Our Weight | Cortex |
|----------|-----------|--------|
| file_hash | 1.0 | Equal |
| url | 0.9 | Equal |
| domain | 0.8 | Equal |
| email | 0.7 | Equal |
| hostname | 0.6 | Equal |
| user | 0.5 | Equal |
| ip | 0.3 | Equal |

A matching malware hash is a much stronger dedup signal than a matching IP address. Cortex doesn't make this distinction.

### 4.2 Graduated Temporal Decay

Cortex is binary: the alert is within the window or it's not. Our 6-bracket decay gives a graduated score:

| Time Gap | Our Score | Cortex |
|----------|----------|--------|
| 0-5 min | 1.0 | In-window (match) |
| 5-30 min | 0.8 | In-window (match) |
| 30-60 min | 0.6 | Possibly expired |
| 1-6 hours | 0.4 | Expired (no match) |
| 6-24 hours | 0.2 | Expired (no match) |
| >24 hours | 0.1 | Expired (no match) |

This means two alerts 3 minutes apart score higher than two alerts 5 hours apart, even if both are technically "within the window." Cortex can't express this nuance.

### 4.3 Cross-Vendor Dedup

Our Layer 2 explicitly handles the case where CrowdStrike and Splunk both report the same C2 callback with the same IOCs but different alert names and different sources. Cortex's fingerprint approach generates different keys per vendor.

### 4.4 Three-Layer Fallback Cascade

Our L1→L2→L3 cascade provides defense in depth:

- L1 catches exact duplicates instantly (fast path)
- L2 catches fuzzy/cross-vendor duplicates via IOC similarity (smart path)
- L3 catches rule-name matches as a last resort (fallback)

Cortex is typically single-method: either fingerprint OR ML, not cascaded.

### 4.5 Block-List Filtering

We filter private IPs (RFC 1918), public DNS resolvers (8.8.8.8, 1.1.1.1), and common domains (microsoft.com, google.com) before comparison. Cortex doesn't mention this. Without filtering, these common values would contribute to false merges across unrelated alerts.

### 4.6 Full IOC Canonicalization

Our canonicalizer handles:
- IPv4: strip CIDR, remove leading zeros (`010.000.001.005/24` → `10.0.1.5`)
- IPv6: expand to full 8-group lowercase hex
- Domains: lowercase, strip trailing FQDN dot
- URLs: lowercase host, remove default ports (80/443)
- Emails/hashes/users: lowercase and trim

Cortex does basic normalization (strip domain suffixes, replace numbers) but not at the IOC-specific level.

---

## 5. Recommendations

Prioritized list of improvements based on this review:

### Priority 1: Per-Alert-Type Dedup Rules

Add a configuration file (YAML or JSON) that maps detection rules to custom dedup key fields and time windows. This is the single biggest improvement we can make.

```yaml
rules:
  "Defender - New High Severity Alerts":
    key_fields: [src_ip, user]
    window_minutes: 15

  "Account Lockout":
    key_fields: [username]
    window_minutes: 10

  "Brute Force Detection":
    key_fields: [src_ip, dest_ip]
    window_minutes: 30

  default:
    key_fields: []  # use all IOCs
    window_minutes: 30
```

### Priority 2: Sliding Time Windows

Replace fixed time buckets in Layer 1 with sliding windows. Compare against the actual timestamp of the stored alert, not the bucket number. This eliminates boundary-straddling false negatives.

### Priority 3: Duplicate Count Tracking and Escalation

Track how many times each canonical alert has been deduplicated. Re-surface alerts to the analyst queue when counts reach escalation thresholds (e.g., 5, 10, 50, 100).

### Priority 4: Formal Duplicate Linking

In the output/database, create explicit parent-child links between canonical alerts and their duplicates. Include metadata: count, time span, contributing sources.

### Priority 5 (Future): Text-Based Similarity

Add a lightweight text similarity check (cosine similarity on TF-IDF vectors of alert descriptions) as a Layer 2.5 between IOC similarity and rule-name matching. This would catch description-similar alerts with different IOCs.

### Priority 6 (Future): ML-Based Dedup

Train a model on labeled dedup pairs (our `dedup_feedback.csv` data) to learn which field combinations predict true duplicates. This replaces hardcoded weights with learned weights.

---

## 6. Sources

- [Dedup - Generic v3 | Cortex XSOAR](https://xsoar.pan.dev/docs/reference/playbooks/dedup---generic-v3) — Playbook-based dedup with ML, rules, and text similarity methods
- [How to Build Alert Deduplication Logic (OneUptime)](https://oneuptime.com/blog/post/2026-01-30-alert-deduplication/view) — Fingerprinting strategies, time windows, storage patterns
- [Cortex XSOAR Incident De-Duplication](https://docs-cortex.paloaltonetworks.com/r/Cortex-XSOAR/6.6/Cortex-XSOAR-Administrator-Guide/Incident-De-Duplication) — Pre-process rules, manual and automatic dedup
- [Cortex XSIAM Alert Deduplication](https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM/Cortex-XSIAM-Documentation/Alert-deduplication) — OOTB dedup feature overview
- [Cortex XSIAM dedup (XQL)](https://docs-cortex.paloaltonetworks.com/r/Cortex-XSIAM/Cortex-XSIAM-Documentation/dedup) — Query-level dedup operator
- [XSIAM Alert Handling Playbooks](https://xsoar.pan.dev/docs/reference/articles/XSIAM-Playbooks) — Alert response and handling framework

---

## 7. Limitations of This Review

- The Cortex XSIAM documentation site (`docs-cortex.paloaltonetworks.com`) requires JavaScript rendering and returned only navigation/table-of-contents content. Detailed XSIAM-specific algorithm documentation could not be fully extracted.
- The XSOAR dedup details come from the v3 playbook (now deprecated in favor of v4). The v4 playbook may have additional capabilities not documented here.
- Cortex XSIAM's OOTB dedup is a proprietary black-box feature. The exact algorithm is not publicly documented. The fingerprinting and time window details in this review are synthesized from community documentation, blog posts, and the XSOAR playbook specifications.
