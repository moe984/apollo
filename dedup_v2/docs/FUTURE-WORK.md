# Dedup Engine: Future Work

Generated: 2026-05-28
Context: Phase 1 of the dedup engine shipped via tekstream-cip/cip#460 (merged 2026-05-20). The engine lives at `cip/services/ml-service/src/services/dedup/` with Layer 1 (hash), Layer 2 (fuzzy), and STIX equivalence cross-validation. This document captures what remains to be ported from Apollo, what research is needed, and what architectural decisions are open.

Parent issue: tekstream-cip/cip-command#632

---

## Phase 1 Delivered (Complete)

What the engine has today (14 source files, 129 tests):

- **Layer 1 (hash match):** Two-stage gate (prerequisites + IOC slot hashes). Confidence = matched/total slots. Threshold: 0.10.
- **Layer 2 (fuzzy match):** Cross-rule Token Sort Ratio via rapidfuzz. Same time bucket, different alert name. Threshold: 10.
- **STIX equivalence:** Context similarity (stix2 object_similarity) + observable similarity (Jaccard intersection). Runs after L1 or L2 match for confidence enrichment.
- **STIX 2.1 parser:** Handles rich bundles (standalone SCOs) and minimal bundles (Splunk vendor extensions). 6 IOC types extracted.
- **IOC normalizer:** Type-specific normalization (IPv6 expansion, URL canonicalization, CIDR stripping, domain/email/hash/user lowercasing).
- **Fingerprinter:** SHA-256 per IOC slot + prerequisites (alert_name, time_bucket).
- **Redis state:** Async client with forward index, inverted index, bundle store, result store. 30-min TTL. Tenant isolation via `dedup:{customer_id}:` prefix.
- **PolicyForge integration (Invariant 2):** Per-customer thresholds with customer_overrides, 5-min cache, static fallback.
- **CIP Event Bus (Invariant 3):** 5 event types (processed, duplicate, similar, new, config_warning). Fire-and-forget.
- **Overwatch metrics (Invariant 4):** 6 metric types (latency, verdict, layer, confidence_tier, slot_fill_rate, error).
- **API:** POST /alerts, GET /alerts/{id}, DELETE /admin/flush, GET /health.

---

## Research Required (Blocking)

### IOC Blocklist Sources (Blocks Task 2.1)

The engine currently scores ALL extracted IOCs. High-noise indicators (private IPs, public DNS resolvers, CDN ranges) inflate false match rates. Apollo had a hardcoded blocklist in `ioc-blocklist.ts`, but we explicitly decided: no hardcoded values without authoritative backing.

**Research needed:**
- RFC 1918 / RFC 6598 / RFC 5737: document exact CIDR ranges for private and reserved IPs
- IANA special-purpose address registry: canonical source for loopback, link-local, broadcast
- Public DNS resolver IPs: source from published provider documentation (Google, Cloudflare, Quad9, OpenDNS)
- Cloud provider IP ranges: AWS (`ip-ranges.json`), Azure (`ServiceTags`), GCP (`cloud.json`), Cloudflare (`/ips-v4`, `/ips-v6`)
- CDN domains: need published domain lists or documented rationale for each entry
- Metadata endpoints: 169.254.169.254 (AWS/GCP/Azure), fd00:ec2::254 (AWS IPv6)

**What Apollo blocked (for reference, not to copy blindly):**
- IPs: RFC 1918 ranges, 8.8.8.8, 1.1.1.1, 9.9.9.9, 208.67.222.222, 208.67.220.220, 0.0.0.0, 127.0.0.1, 255.255.255.255, 169.254.x.x/16
- Domains: microsoft.com, windows.com, google.com, googleapis.com, github.com, amazon.com, amazonaws.com, cloudflare.com, akamaized.net, fastly.net, cloudfront.net, example.com, localhost

**Deliverable:** A document with every blocklist entry linked to its authoritative source (RFC number, provider URL, IANA registry). Present for review before coding.

### IOC Type Weighting Basis (Blocks Task 2.4)

The engine currently treats all IOC slots equally in Layer 1 confidence scoring. Apollo used weighted Jaccard in `similarity-scorer.ts` with weights from file_hash=1.0 down to ip=0.3. These weights were not backed by published research.

**Research needed:**
- MITRE ATT&CK data on indicator reliability by type
- Academic papers on IOC fidelity and false positive rates by indicator type
- Detection engineering community best practices (SANS, FIRST, published SOC playbooks)
- Statistical analysis from Apollo's own alert data (if available): which IOC types most reliably distinguish unique incidents vs duplicates

**What Apollo used (for reference):**
- file_hash: 1.0, url: 0.9, domain: 0.8, email: 0.7, hostname: 0.6, user: 0.5, ip: 0.3

**Deliverable:** A document citing published sources for each weight value. Present for review before coding.

---

## Ready to Build (No Blockers)

### Layer 3: Use-Case Dedup (Task 2.2)

Same detection rule + same source within a configurable time window = likely duplicate. This catches alerts that share no IOCs but describe the same incident from the same detection pipeline.

**What to port from Apollo:**
- `rule-normalizer.ts`: Multi-step normalization (strip vendor prefixes like "TS -", "Endpoint -", "ESCU -"; strip suffixes like " - Rule", " - Alert"; collapse whitespace). 60+ canonical use-case registry with per-rule dedup keys and default windows.
- `use-case-index.ts`: Redis sorted sets keyed by `{customer}:{source}:{canonical_use_case}`. Score = timestamp. `ZREVRANGEBYSCORE` for time-window queries. Window resolution: PolicyForge per-use-case override, then registry default, then flat fallback.

**Engine integration:**
- Runs after Layer 1 + Layer 2 miss
- New Redis key pattern: `dedup:{customer_id}:usecase:{source}:{canonical_rule}` (sorted set)
- Result goes into `score_breakdown.use_case_match`
- Verdict: DUPLICATE with `layer=3`

### Layer 4: Entity Baseline Suppression (Task 2.3)

If a primary entity (IP, user, hostname) generates alerts within its historical frequency baseline, suppress below-threshold alerts as noise. This handles "chatty" entities that generate the same alerts every day.

**What to port from Apollo:**
- `entity-baseline.ts`: Rolling average model. Per-entity Redis keys tracking total observations, daily average, today's count, active days. Suppression logic: learning period (min 5 observations), anomaly detection (today > daily_avg * 1.5 = anomaly, don't suppress), daily cap (max 100 suppressions).

**Engine integration:**
- Runs after Layer 3 miss
- New Redis key patterns: `dedup:{customer_id}:baseline:{source}:{entity_type}:{entity_value}:*`
- TTL: (learning_period + 7 days) * 24h
- Result goes into `score_breakdown.entity_baseline`
- Verdict: DUPLICATE with `layer=4`
- Opt-in per customer via PolicyForge (default: disabled)

### Response Contract Update (Task 2.5)

Expand `AlertOut` schema for the new layers:
- `layer` field: add values 3 (use-case) and 4 (entity baseline)
- `score_breakdown` expanded with `use_case_match` dict (matched_rule, canonical_use_case, source, time_window_seconds, matched_alert_id) and `entity_baseline` dict (entity_type, entity_value, current_rate, baseline_rate, threshold_exceeded, lookback_window_hours)
- Update Pydantic models and OpenAPI schema

### Tests (Task 2.7)

- `test_ioc_blocklist.py`: blocklist filtering for each category
- `test_layer3_use_case.py`: rule normalization + use-case matching with windows
- `test_layer4_entity_baseline.py`: frequency tracking, learning period, anomaly detection, daily cap
- `test_ioc_weighting.py`: weighted Jaccard scoring
- `test_full_pipeline_4_layers.py`: end-to-end with all layers + STIX equivalence
- Integration: alert that misses L1+L2 but matches L3
- Integration: noisy entity suppressed by L4
- Apollo integration: pipeline calls service, receives verdict, acts correctly

---

## Apollo Cleanup (After Everything Above)

### Remove Apollo's Local Dedup (Task 2.6)

Delete all files in Apollo's `src/ingestion/dedup/`:
- `dedup.ts`, `intelligent-dedup.ts`, `ioc-index.ts`, `ioc-canonicalizer.ts`
- `similarity-scorer.ts`, `use-case-index.ts`, `rule-normalizer.ts`
- `entity-baseline.ts`, `dedup-config.ts`, `ioc-blocklist.ts`
- `known-good-suppressor.ts`, `composite-key.ts`, `explanation-generator.ts`
- `campaign-targets.ts`, `suppression-review-queue.ts`, `dedup-learning-worker.ts`
- `lua-scripts.ts`, `dedup-service-client.ts`, `soar-writeback.ts`
- `composite-key.test.ts`

Remove Phase 1 fallback logic from Apollo's `pipeline.ts`. Apollo becomes a pure HTTP consumer of ml-service `/api/v1/dedup/alerts`.

**Pre-requisite:** All 4 layers verified in engine. Apollo's HTTP client (task 1.10) wired and tested.

---

## Open Architectural Questions

### Q1: Known-Good Suppression

Apollo has `known-good-suppressor.ts`: a software whitelist check that runs BEFORE dedup. If an alert matches a known-good pattern, it's suppressed without scoring. This is a classification decision (matches "engine decides" principle), but it's tightly coupled to SOAR/RunbookPolicy configuration in Apollo.

**Decision needed:** Move to dedup engine, or keep in Apollo?

### Q2: Dynamic Threshold Delivery

Apollo's `dedup-learning-worker.ts` adjusts thresholds per customer based on analyst feedback (FP rate > 20% with sample >= 5 triggers +0.05 bump, max 0.95). The feedback loop stays in Apollo, but how should the engine consume those thresholds?

**Options:**
- (a) Apollo passes dynamic thresholds in the request body alongside the STIX bundle
- (b) Engine reads from PolicyForge (learning worker writes to PolicyForge)
- (c) Engine reads from Redis directly (learning worker writes to shared key)
- (d) Deferred to Phase 3

### Q3: Temporal Decay vs Fixed Time Buckets

Current engine uses fixed 30-min time buckets. Apollo used temporal decay with continuous scoring:
- 0-5min: 1.0
- 5-30min: 0.8
- 30min-2hr: 0.6
- 2-24hr: 0.3
- 24hr+: 0.0

Combined score was 80% Jaccard + 20% temporal + 0.15 use-case bonus (capped at 1.0). Temporal decay is more accurate but adds complexity.

**Decision needed:** Phase 2 or Phase 3?

### Q6: Latency Budget

Apollo enforces a 5ms hard deadline per alert after candidate discovery. The network hop to ml-service adds latency. What's the acceptable SLA? This affects connection pooling, potential gRPC migration, Lua scripts for atomic operations, and local caching strategy.

### Q7: 30-Minute Window (apollo#212 Audit Findings)

The 30-min hash dedup window (`DEFAULT_DEDUP_WINDOW_MS = 1_800_000`) has no data-driven justification. A production audit (CUST-LSUAM, 422 alerts, 2026-05-12) was conducted to validate it. The audit could not answer the original question (optimal window duration) but exposed deeper problems.

**Production data pull (CUST-LSUAM, 422 alerts):**
- 405/422 alerts from Splunk, 17 from generic. Zero CrowdStrike, Sentinel, M365 Defender, Elastic, Entra ID.
- 15 hash-dedup events total: 14 were test alerts (`dedup-test-*`), 1 was a real production hit.
- The 1 real production hit was a **confirmed false positive**.
- Hash dedup had **zero confirmed true-positive catches** on real production alerts.

**Confirmed false positive:**
Two different Defender alerts (different NotebookLM URLs, different Splunk events, 10 minutes apart) were incorrectly merged. The Splunk adapter's `extractObservables()` doesn't extract URL-type IOCs. When `IOC_Type=URL` and no IP fields are populated, the hash collapsed to `(empty)|(empty)|rule_name|time_bucket`. Any Defender alert with the same rule name in the same 30-min window collided regardless of actual content.

**Scale of the problem:**
- 57/90 real dedup pairs were false positives, all from one rule: "Threat - LSU - Defender - New High Severity Alerts - Rule"
- 11 real users had security alerts silently suppressed
- Filed as separate bug: tekstream-cip/apollo#219

**Fuzzy dedup circular dead zone:**
- Only HIGH severity alerts have IOC fingerprints in the index
- HIGH alerts bypass fuzzy dedup (severity guard)
- MEDIUM (107) and INFO (68) alerts are eligible for fuzzy matching but have zero candidates
- Intelligent dedup has **never produced a match in production**

**Zero analyst feedback:**
- `dedup_feedback` table is empty
- The learning worker threshold adjustment loop has no data to work from

**Root cause (addressed in Phase 1 engine rewrite):**
- No input validation before hash dedup: pipeline passed empty observables directly to `dedup.check()`
- Adapter didn't extract URL IOCs, userPrincipalName, incidentId, or title into hash input
- Phase 1 engine (`cip/services/ml-service/src/services/dedup/`) rewrites STIX parsing to extract all 6 IOC types from both SCOs and vendor extensions, preventing the empty-hash collision

**Window analysis status:**
- Cannot derive optimal window duration: only 15 dedup events (14 test, 1 FP) is too small for P95 analysis
- Need more production volume before time-gap distribution is meaningful
- The engine supports configurable windows in `dedup_defaults.yaml` but not per-customer via PolicyForge yet
- Optimal window sits between P95 of true duplicate gaps and minimum true recurrence gap; both distributions are unknown with current data

---

## Capabilities in Apollo Not Yet Scoped

These exist in Apollo's dedup directory but are not mentioned in any Phase 2 task. They need to be explicitly scoped into Phase 2, Phase 3, or declared out-of-scope.

| Capability | Apollo File | What It Does |
|---|---|---|
| Explanation generator | `explanation-generator.ts` | Plain-English dedup verdicts for SOC analysts ("This alert shares 3 file hashes with an existing alert from CrowdStrike, received 12 minutes ago. Similarity: 94%.") |
| Campaign target aggregation | `campaign-targets.ts` | Merge target entities (user, host, email, IP) across duplicate alerts. 500 target cap. Keeps earliest firstSeen. |
| Suppression review queue | `suppression-review-queue.ts` | Routes borderline-confidence verdicts to analyst batch review queue. Feedback collected for threshold adjustment. |
| Learning worker | `dedup-learning-worker.ts` | Queries dedup_feedback table, calculates per-method FP rate, adjusts thresholds (+0.05 if FP > 20%, min sample 5). Stores in Redis with 7-day TTL. |
| Composite key with disambiguators | `composite-key.ts` | SHA-256 composite key generation with disambiguator fields for incident-level dedup. Backward compatibility guard for key shape. |
| Severity guard | `intelligent-dedup.ts` | HIGH/CRITICAL alerts bypass dedup entirely (never auto-suppressed). |
| Min IOC types guard (SCORE-04) | `similarity-scorer.ts` | Requires >= 2 shared IOC types minimum to prevent single-indicator false merges. |
| Lua scripts | `lua-scripts.ts` | Atomic check-and-set for Redis race conditions (DEDUP_CHECK_AND_SET, FIND_SIMILAR, REGISTER_IOCS). |

---

## Layer 3: Semantic Matching (ML, Future)

Documented separately in `BACKLOG.md`. Uses embedding models (sentence-transformers or security-specific) for cosine similarity on alert text. Catches same-meaning/different-words cases that hash and fuzzy miss. Not started. Requires ML model selection, vector store, and inference latency budget.

---

## STIX-Ray Integration

STIX-Ray (`poc/stix-ray/`) provides human-in-the-loop validation for dedup output. Future integration points:

- **URL fetch input:** Pull bundles directly from the engine's output for visual comparison
- **Automated regression harness:** Run STIX-Ray compare endpoint against known bundle pairs on every algorithm change
- **Feedback loop:** Analyst verdicts in STIX-Ray feed back to the learning worker for threshold calibration

See tekstream-cip/poc#2 for STIX-Ray status and future work.
