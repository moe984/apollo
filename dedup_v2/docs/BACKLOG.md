# Backlog

## Layer 3 — Cosine Similarity (Semantic Matching)

### Problem

Layer 1 (hash) catches exact IOC matches. Layer 2 (fuzzy) catches character-level string overlap across different rules. Neither understands **meaning**.

### What Layer 3 Would Catch

Alerts that describe the same thing in different words or with minor variations that hash and fuzzy matching miss.

**Example 1 — Same meaning, different words:**
```
Alert A: "Unfamiliar sign-in properties detected for user lancescott"
Alert B: "Suspicious login from unknown device for account lancescott"

Layer 1: different hashes → miss
Layer 2: fuzzy ~40% (some shared words)
Layer 3: cosine ~90%+ (same meaning — suspicious login for same user)
```

**Example 2 — Same person, different format:**
```
Alert A: user = lancescott@lsu.edu
Alert B: user = lance.scott@lsu.edu

Layer 1: different hash → miss
Layer 2: fuzzy ~80% (similar characters)
Layer 3: cosine ~95%+ (same person at same org)
```

**Example 3 — Same technique, different description:**
```
Alert A: "Password spray attack detected"
Alert B: "Brute force credential stuffing"

Layer 1: different hash → miss
Layer 2: fuzzy ~30% (low character overlap)
Layer 3: cosine ~85%+ (same attack technique)
```

### How It Would Work

1. Take the alert's IOC values + rule name + description
2. Feed through an embedding model (e.g., sentence-transformers or security-specific model)
3. Get a vector (e.g., 384 dimensions)
4. Compare against stored alert vectors using cosine similarity
5. Score 0-1 — how semantically similar are these two alerts

### Three-Layer Stack

| Layer | Method | Speed | What it catches |
|-------|--------|-------|-----------------|
| 1 — Hash | Per-slot SHA-256 | Fast | Exact duplicates (same rule, same IOCs) |
| 2 — Fuzzy | Token Sort Ratio | Medium | Cross-rule correlation (different rule, similar IOCs) |
| 3 — Semantic | Cosine similarity | Slower | Same meaning, different words (requires ML model) |

Each layer catches what the previous layers miss. The flow:

```
Layer 1: exact match? → DUPLICATE
    ↓ No
Layer 2: fuzzy match? → SIMILAR
    ↓ No
Layer 3: semantic match? → RELATED
    ↓ No
NEW
```

### Requirements

- ML embedding model (sentence-transformers, security-specific, or fine-tuned)
- Vector store (Redis with vector search, or dedicated like Pinecone/Weaviate)
- Inference latency budget — embedding generation adds ~10-50ms per alert
- Training/fine-tuning data for security-specific embeddings

### Where It Fits

Layer 3 is the ML component that would live under CIP ML services. The ML team owns the model, the embeddings, and the similarity threshold. The dedup engine calls it as a service.

### Status

Not started. Documented as future work. See tekstream-cip/apollo#294 for the integration discussion.

---

## Open Items

### Confidence Threshold Validation
- Layer 1 hash confidence threshold: currently `0.10` for testing, needs production validation
- Layer 2 fuzzy similarity threshold: currently `0.10` for testing, needs tuning
- Need a gold set of labeled alert pairs to measure precision/recall at different thresholds

### Queue/Buffer
- Depending on alert volume, a queue in front of the API may be needed
- Need to determine expected alerts per second/minute/hour
- Real-time vs near-real-time decision pending

### Human-in-the-Loop Validation
- STIX Visualizer (STIX-Ray) integration for analysts to compare parent and duplicate side-by-side
- Feedback loop to adjust thresholds based on analyst corrections

### Production Hardening
- Lua-based atomic Redis operations (prevent race conditions)
- Latency budget measurement (5ms target)
- Fail-open safety (Redis down → treat as NEW)
- Tenant isolation validation
