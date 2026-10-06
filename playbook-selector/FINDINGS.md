# Playbook Selector Model — Investigation Findings

**Date:** 2026-05-01
**Investigator:** Moe (AI/ML Architect, CIP Team)
**Status:** Critical gaps identified — model is live but disconnected from training pipeline

---

## 1. Overview

The Playbook Selector is one of 6 ONNX models in the CIP ML inference service. It recommends which response playbook to execute for a given detection/incident. The model is deployed and serving predictions in production, but investigation reveals significant disconnects between the training pipeline, the deployed model, and the playbook catalog.

---

## 2. Architecture Trace

```
Spoke: sp-intelligence (packages/sp-intelligence)
  → Inference Client calls ML service
    → Gateway API (packages/gateway-api, port 40000)
      → CloudFront (https://d37lzn65ul43gr.cloudfront.net)
        → ML Inference Service (services/ml-services/ml-inference, port 40020)
          → ONNX Runtime (playbook_selector model)
        → ML Monitoring Service (services/ml-services/ml-monitoring)
          → COSMOS Dashboard (packages/dashboard-cosmos)
```

### Key Files

| Component | File |
|-----------|------|
| Model registration | `ml-inference/src/inference/mlflow_loader.py:23` |
| Model output handling | `ml-inference/src/inference/engine.py:115-118` |
| Model config (cache TTL) | `ml-inference/src/config/__init__.py:54` |
| Prediction schemas | `ml-inference/src/inference/schemas.py:21-28` |
| Inference API route | `ml-inference/src/api/routes/inference.py:38-73` |
| Gateway proxy (incident) | `gateway-api/src/routes/inference.ts:389-505` |
| Gateway proxy (cosmos) | `gateway-api/src/routes/cosmos.ts` |
| Feature extractors | `sp-intelligence/src/feature-store/feature-extractors.ts:290-395` |
| Feature types | `sp-intelligence/src/feature-store/types.ts:101-157` |
| Training data prep | `sp-intelligence/src/training/data-prep.ts:59-77, 665-687` |
| Training job runner | `sp-intelligence/src/training/job-runner.ts:68-71, 95` |
| Training evaluator | `sp-intelligence/src/training/evaluator.ts:352-354` |
| Copilot context builder | `sp-intelligence/src/copilot/context-builder.ts:590-598` |
| Action suggester | `sp-intelligence/src/copilot/action-suggester.ts:314-333` |
| Playbook templates | `workflow-orchestrator/src/playbooks/library/` |
| Playbook DB | `workflow-orchestrator/src/playbooks/db.ts` |
| Playbook routes | `workflow-orchestrator/src/routes/playbooks.ts` |
| Telemetry generator | `scripts/generate-inference.py:93-102` |

---

## 3. Production Model — What's Actually Deployed

### Model Info (from live API)

```
Name:           playbook_selector
Version:        bundled
Stage:          production
Entity types:   detection, incident
Cache TTL:      14400s (4 hours)
A/B testing:    inactive
```

### Feature Schema (11 features — from `GET /api/inference/models`)

| # | Feature | Type | Range | Description |
|---|---------|------|-------|-------------|
| 1 | `detection_type_encoded` | int | 0-8 | Encoded detection category |
| 2 | `severity_level` | int | 1-4 | Severity level |
| 3 | `confidence_score` | float | 0.30-0.99 | Detection confidence |
| 4 | `affected_asset_type_encoded` | int | 0-5 | Encoded asset type |
| 5 | `playbook_type_encoded` | int | 0-6 | Encoded playbook category |
| 6 | `playbook_complexity` | float | 0.1-1.0 | Playbook complexity score |
| 7 | `playbook_avg_effectiveness` | float | 0.4-0.95 | Historical avg effectiveness |
| 8 | `playbook_usage_count_30d` | int | 0-100 | Usage count in last 30 days |
| 9 | `detection_playbook_type_match` | float | 0.0-1.0 | Detection-playbook match score |
| 10 | `historical_success_rate` | float | 0.3-0.95 | Historical success rate |
| 11 | `similar_detection_playbook_success` | float | 0.2-0.9 | Success rate on similar detections |

### Model Output

Special output handling in `engine.py:115-118`:
```python
elif self.model_name == "playbook_selector":
    if len(outputs) >= 2 and outputs[1] is not None:
        return np.array(outputs[1], dtype=np.float32).flatten()
    return outputs[0].flatten().astype(np.float32)
```

Telemetry shows predictions like:
```json
{
  "playbook_confidence": 0.694,
  "recommended_playbook": "pb-ransomware-containment"
}
```

---

## 4. Training Pipeline — What sp-intelligence Trains With

### Feature Schema (15 features — from `data-prep.ts:665-687`)

**Creation features (8):**

| # | Feature | Type | Default |
|---|---------|------|---------|
| 1 | `priorityLevel` | int (1-4) | 2 |
| 2 | `detectionCount` | int | 1 |
| 3 | `affectedAssetCount` | int | 1 |
| 4 | `initialSeverity` | int (1-10) | 5 |
| 5 | `hasPlaybook` | bool→0/1 | 0 |
| 6 | `isAutoCreated` | bool→0/1 | 0 |
| 7 | `correlationScore` | float (0-1) | 0.5 |
| 8 | `attackStageDepth` | int (1-7) | 1 |

**Response features (7):**

| # | Feature | Type | Default |
|---|---------|------|---------|
| 9 | `timeToAcknowledgeMs` | int (ms) | 0 |
| 10 | `escalationCount` | int | 0 |
| 11 | `playbookCount` | int | 0 |
| 12 | `analystAssignments` | int | 1 |
| 13 | `containmentActionsCount` | int | 0 |
| 14 | `investigationNotesCount` | int | 0 |
| 15 | `externalReferenceCount` | int | 0 |

### Training Label

```typescript
'playbook-selector': (labels) => (labels.playbookEffective === true ? 1 : 0)
```

Binary label from `intelligence.incident_feedback.playbook_effective` column.

### Training Config

```
Schedule:       Monthly (1st at 5am UTC)
Hyperparameters: n_estimators=100, max_depth=10, lr=0.1, min_samples_split=5
Evaluation:     NDCG >= 0.70 threshold
Data source:    intelligence.incident_features (PostgreSQL)
Feedback:       intelligence.feedback.playbook_effective
```

---

## 5. Feature Schema Mismatch

**The production model and training pipeline use completely different features. Zero overlap.**

| # | Production Model (ONNX) | Training Pipeline (sp-intelligence) |
|---|-------------------------|--------------------------------------|
| 1 | `detection_type_encoded` | `priorityLevel` |
| 2 | `severity_level` | `detectionCount` |
| 3 | `confidence_score` | `affectedAssetCount` |
| 4 | `affected_asset_type_encoded` | `initialSeverity` |
| 5 | `playbook_type_encoded` | `hasPlaybook` |
| 6 | `playbook_complexity` | `isAutoCreated` |
| 7 | `playbook_avg_effectiveness` | `correlationScore` |
| 8 | `playbook_usage_count_30d` | `attackStageDepth` |
| 9 | `detection_playbook_type_match` | `timeToAcknowledgeMs` |
| 10 | `historical_success_rate` | `escalationCount` |
| 11 | `similar_detection_playbook_success` | `playbookCount` |
| 12 | — | `analystAssignments` |
| 13 | — | `containmentActionsCount` |
| 14 | — | `investigationNotesCount` |
| 15 | — | `externalReferenceCount` |

---

## 6. Playbook Catalog Mismatch

### Playbooks the model recommends (from live telemetry):

- `pb-ransomware-containment`
- `pb-data-exfil-investigate`
- `pb-phishing-triage`
- `pb-lateral-movement`
- `pb-malware-response`
- `pb-brute-force-block`

### Playbooks in workflow-orchestrator catalog:

- Alert Escalation (`alert-escalation-v1`)
- Compliance Check (`compliance-check-v1`)
- Incident Response (`incident-response-v1`)

### Playbooks hardcoded in gateway inference route (`inference.ts:461-464`):

- `pb-malware-containment`
- `pb-network-isolation`
- `pb-forensic-collection`
- `pb-credential-reset`

**Three different sets of playbook IDs. None of them match each other.**

---

## 7. Broken Integration Points

### 7.1 Copilot Never Calls Playbook Selector

`context-builder.ts:590-598`:
```typescript
private getApplicableModels(entityType: string): ModelType[] {
  switch (entityType) {
    case 'detection':
      return ['fp_predictor', 'severity_calibrator'];
    case 'incident':
      return ['mttr_predictor'];  // playbook_selector NOT included
    default:
      return [];
  }
}
```

### 7.2 ActionSuggester Uses Static Data, Not ML

`action-suggester.ts:314-333` gets playbook candidates from `context.relatedEntities` (static data), not from the playbook_selector model predictions.

### 7.3 No Feature Schema in MLflow Loader

`mlflow_loader.py` — playbook_selector is listed as `STANDARD_MODEL` but missing from `_MODEL_FEATURE_SCHEMAS`. Returns empty `[]`.

### 7.4 No Feast Feature Mapping

`feature_client.py` — `MODEL_FEATURE_REFS` has no entry for playbook_selector. No feature auto-enrichment.

### 7.5 No Link to Playbook Catalog

sp-intelligence never queries `workflow.playbook_templates` or `workflow.playbook_instances`. The model has no awareness of what playbooks actually exist for a customer.

---

## 8. Live Data Analysis

### API Access

```bash
# Auth: service header (no JWT needed)
-H "X-CIP-Service: sp-intelligence"

# Base URL
https://d37lzn65ul43gr.cloudfront.net
```

### Available Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/cosmos/history?model_name=playbook_selector&page=1&page_size=200` | GET | Paginated telemetry history |
| `/api/cosmos/models/health/full` | GET | Model health (latency, drift, cache) |
| `/api/cosmos/models/drift/heatmap` | GET | Per-feature drift |
| `/api/inference/models` | GET | All models with feature schemas |
| `/api/inference/models/playbook_selector` | GET | Single model info |
| `/api/inference/incident/:id` | GET | Incident prediction + playbook recs |

### Data Volume

- **303,910 total playbook_selector events** in telemetry store
- 3 customers: `cust-acme-corp-002`, `cust-globex-003`, `cust-tekstream-001`
- Events from: 2026-03-27 (generated by `scripts/generate-inference.py`)
- Roughly even distribution across customers (~33% each)
- Roughly even distribution across 6 playbooks (~16% each)
- Confidence range: 0.500 – 0.990 (avg 0.742)
- Latency range: 5.4ms – 199.8ms (avg 102.4ms)

### Key Observation

All telemetry data was generated by `scripts/generate-inference.py` using randomized features. There is no real production traffic. The data is synthetic.

---

## 9. Database Schema

### Feature Store (intelligence schema)

```sql
-- Incident features (training input)
intelligence.incident_features (
  feature_id UUID, incident_id UUID, customer_id VARCHAR,
  creation JSONB,   -- priorityLevel, detectionCount, etc.
  response JSONB,   -- timeToAcknowledgeMs, escalationCount, etc.
  outcome JSONB,    -- resolutionTimeMs, playbookEffectiveness, etc.
  labels JSONB,     -- playbookEffective (training label)
  feature_time TIMESTAMPTZ, event_time TIMESTAMPTZ
)

-- Analyst feedback (training labels)
intelligence.incident_feedback (
  feedback_id UUID, incident_id VARCHAR, customer_id VARCHAR,
  playbook_id VARCHAR, playbook_effective BOOLEAN,
  resolution_quality VARCHAR, actual_mttr_minutes DECIMAL,
  ...
)

-- AI recommendation tracking
intelligence.ai_feedback (
  recommendation_type VARCHAR,  -- includes 'playbook_suggestion'
  model_id VARCHAR, accepted BOOLEAN, confidence DECIMAL,
  ...
)
```

### Playbook Catalog (workflow schema)

```sql
-- Global templates
workflow.playbook_templates (
  playbook_id UUID, name VARCHAR, category VARCHAR,
  version INT, variables JSONB, workflow_template JSONB
)

-- Per-customer instances
workflow.playbook_instances (
  instance_id UUID, playbook_id UUID, customer_id VARCHAR,
  name VARCHAR, variable_values JSONB, workflow_id UUID, enabled BOOLEAN
)
```

### Telemetry (ml-monitoring)

```sql
-- Inference telemetry (NO input features stored)
public.inference_telemetry (
  id UUID, timestamp TIMESTAMPTZ, model_name VARCHAR,
  entity_id VARCHAR, customer_id VARCHAR,
  prediction_value JSONB,  -- output only
  confidence FLOAT, latency_ms FLOAT, cache_hit BOOLEAN
)
```

**Note:** Input features are NOT persisted after prediction. Only the output is stored.

---

## 10. Summary of Gaps

| # | Gap | Severity | Impact |
|---|-----|----------|--------|
| 1 | Feature schema mismatch (11 prod vs 15 training, zero overlap) | Critical | Training produces a model incompatible with inference |
| 2 | Playbook IDs mismatch (3 different sets across systems) | Critical | Model recommends playbooks that don't exist in catalog |
| 3 | Copilot never calls playbook_selector | High | Model is deployed but unused by the application |
| 4 | ActionSuggester uses static data, not ML predictions | High | Playbook suggestions are not ML-driven |
| 5 | No feature schema in MLflow loader | Medium | No input validation at inference time |
| 6 | No Feast feature mapping | Medium | No feature auto-enrichment from store |
| 7 | Input features not persisted | Medium | Cannot audit or debug what the model received |
| 8 | All telemetry data is synthetic | Info | No real production traffic to validate against |
| 9 | Entity type inconsistency (detection vs incident) | Low | Telemetry generator sends as detection; training uses incident |

---

## 11. Blockers for Building a Real Model

### Blocker 1: No Feedback Loop

The model depends on `playbook_effective` labels from analysts. The database table exists (`intelligence.incident_feedback`) but:

- No evidence that analysts are submitting feedback in production
- The implicit feedback table tracks `playbook_cancelled` and `playbook_modified` but we don't know if those signals are flowing
- Without labels, there is nothing to train on

**Verification:** Run `check_data_availability.py` against the cloud database.

### Blocker 2: No Playbook Execution Tracking

There is no record of **which playbook was executed on which incident**:

- `intelligence.incident_feedback` has a `playbook_id` column, but nothing writes to it automatically
- `workflow-orchestrator` runs playbooks but does NOT write back to `intelligence.incident_feedback`
- Without execution records, we cannot build training pairs (incident + playbook → effective/not)

**Fix required:** The workflow-orchestrator needs to emit a CIP event when a playbook runs, and sp-intelligence needs to capture it in the feedback table.

### Blocker 3: The ML Problem Is Undefined

The current codebase is confused about what the model should do:

| Component | Treats it as | Evidence |
|-----------|-------------|----------|
| Training pipeline (`data-prep.ts`) | Binary classifier | Label: `playbookEffective === true ? 1 : 0` |
| Production model (`engine.py`) | Recommender | Outputs: `recommended_playbook` + `playbook_confidence` |
| Evaluator (`evaluator.ts`) | Ranker | Metric: NDCG (ranking quality) |
| Gateway (`inference.ts`) | Ranker | Calls `/api/v1/predict/playbooks` with candidate list |

These are three different ML problems. Must pick one before training.

---

## 12. Recommended ML Approach

### Decision: Playbook Ranker (Learning-to-Rank)

After evaluating the three options, the **ranker approach** is the best fit for this use case:

#### Why not a binary classifier?

A binary classifier ("will this playbook be effective?") answers the wrong question. Analysts don't ask "is this playbook good?" — they ask "which playbook should I run?" A binary classifier would need to be called N times (once per candidate playbook) and doesn't naturally produce a ranking.

#### Why not a multi-class recommender?

A multi-class recommender ("pick one of 6 playbooks") is fragile. Adding a new playbook requires retraining the model. It also doesn't account for customer-specific playbook catalogs — not every customer has the same playbooks available.

#### Why a ranker?

A ranker takes an incident + a list of candidate playbooks and scores each one. This is exactly what the gateway already expects (`/api/v1/predict/playbooks` with `candidate_playbooks` list). Benefits:

- **Catalog-aware:** Candidates come from `workflow.playbook_instances` for the customer
- **Extensible:** New playbooks don't require retraining — they just enter the candidate list
- **Matches the evaluator:** NDCG is already the evaluation metric
- **Matches the gateway:** `inference.ts:454-474` already sends candidates and expects ranked results

### Proposed Architecture

```
Incident occurs
  → Extract incident features (from feature store)
  → Query workflow.playbook_instances for customer's enabled playbooks
  → For each candidate playbook, extract playbook features:
      - playbook category, complexity, historical effectiveness
      - match score between incident type and playbook type
      - usage count, success rate on similar incidents
  → Concatenate: [incident_features | playbook_features] per candidate
  → Score each candidate with the model
  → Rank by score, return top 3
```

### Proposed Feature Schema (unified)

**Incident features (from feature store — 8 features):**

| Feature | Source | Type |
|---------|--------|------|
| `severity_level` | incident.creation.initialSeverity | int 1-10 |
| `priority_level` | incident.creation.priorityLevel | int 1-4 |
| `detection_count` | incident.creation.detectionCount | int |
| `affected_asset_count` | incident.creation.affectedAssetCount | int |
| `attack_stage_depth` | incident.creation.attackStageDepth | int 1-7 |
| `has_playbook` | incident.creation.hasPlaybook | 0/1 |
| `correlation_score` | incident.creation.correlationScore | float 0-1 |
| `is_auto_created` | incident.creation.isAutoCreated | 0/1 |

**Playbook features (from workflow-orchestrator + historical — 6 features):**

| Feature | Source | Type |
|---------|--------|------|
| `playbook_category_encoded` | playbook_templates.category | int 0-5 |
| `playbook_complexity` | Derived from step count in workflow | float 0-1 |
| `playbook_avg_effectiveness` | Historical from incident_feedback | float 0-1 |
| `playbook_usage_count_30d` | Count from incident_feedback | int |
| `incident_playbook_match` | Cosine similarity of incident type ↔ playbook category | float 0-1 |
| `similar_incident_success` | Success rate on similar past incidents | float 0-1 |

**Total: 14 features per (incident, playbook) pair.**

### Training Data Structure

Each training sample is a **(incident, playbook, label)** triple:

```
incident_id | playbook_id | features[14] | label
INC-001     | pb-abc      | [...]        | 1 (effective)
INC-001     | pb-def      | [...]        | 0 (not effective / not chosen)
INC-002     | pb-abc      | [...]        | 0
INC-002     | pb-ghi      | [...]        | 1
```

Labels come from:
- **Explicit:** `incident_feedback.playbook_effective` = true/false
- **Implicit positive:** Playbook ran to completion without cancellation
- **Implicit negative:** `playbook_cancelled` or `playbook_modified` actions

### Training Pipeline Changes

1. **`data-prep.ts`** — Add `preparePlaybookRankingData()` that:
   - Queries `intelligence.incident_features` for incident features
   - Joins with `intelligence.incident_feedback` for playbook execution records
   - Queries `workflow.playbook_templates` for playbook features
   - Creates (incident, playbook) pairs with labels
   - Temporal split (last 7 days = test set)

2. **`job-runner.ts`** — Update playbook-selector config to use LambdaMART or XGBoost ranker

3. **`evaluator.ts`** — Already uses NDCG, no changes needed

4. **`mlflow_loader.py`** — Add the 14-feature schema to `_MODEL_FEATURE_SCHEMAS`

5. **`feature_client.py`** — Add Feast feature references

6. **`context-builder.ts:595`** — Add `'playbook_selector'` to `getApplicableModels('incident')`

### Integration Changes

1. **workflow-orchestrator** → Emit `playbook.executed` event with `incident_id` + `playbook_id` + outcome
2. **sp-intelligence** → Listen for `playbook.executed` events, write to `incident_feedback`
3. **action-suggester** → Use ML prediction scores instead of static `matchScore`
4. **Gateway inference route** → Query `workflow.playbook_instances` for customer's playbooks as candidates

### Minimum Viable Training Data

| Data | Minimum | Ideal |
|------|---------|-------|
| Incidents with features | 500 | 5,000+ |
| Playbook execution records | 200 | 2,000+ |
| Distinct playbooks | 3 | 10+ |
| Customers | 2 | 10+ |
| Time span | 30 days | 6+ months |

---

## 13. Tools

### fetch_playbook_data.py

Fetch playbook_selector telemetry from the COSMOS API (stdlib only, no pip install):

```bash
python3 fetch_playbook_data.py                                    # first page, 200 events
python3 fetch_playbook_data.py --all --max 2000                   # fetch up to 2000 events
python3 fetch_playbook_data.py --all --max 2000 --output data.json
python3 fetch_playbook_data.py --customer cust-acme-corp-002
python3 fetch_playbook_data.py --all --max 500 --json             # raw JSON output
```

### check_data_availability.py

Check the cloud database for real training data (stdlib only, requires `aws` CLI and `psql`):

```bash
# Auto-fetch credentials from AWS Secrets Manager
python3 check_data_availability.py

# Manual connection
python3 check_data_availability.py --db-url "postgresql://user:pass@host:5432/db"

# Save full report
python3 check_data_availability.py --output data_report.json
```

Checks performed:
1. Schema existence (intelligence, workflow)
2. Incident features count, date range, customer breakdown
3. Feedback labels (explicit + implicit + AI recommendation acceptance)
4. Playbook catalog (templates + customer instances)
5. Training job history
6. Verdict: can we train or not, with specific blockers

---

## 14. Conclusion

**Status: BLOCKED — Cannot proceed with a real model.**

Investigation on 2026-05-01 confirmed that the Playbook Selector model cannot be rebuilt with real data at this time. The blockers are foundational, not incremental:

1. **No real training data** — all 303,910 telemetry events are synthetic (from `generate-inference.py`)
2. **No feedback loop** — analysts are not submitting `playbook_effective` labels, and the cloud DB could not be verified (AWS access issue)
3. **No execution tracking** — workflow-orchestrator does not record which playbook ran on which incident
4. **Feature schema mismatch** — the deployed ONNX model (11 features) and training pipeline (15 features) have zero overlap
5. **Playbook catalog mismatch** — the model recommends 6 playbook IDs that don't exist in any system

### What Must Happen Before Revisiting

| # | Action | Owner | Prerequisite for |
|---|--------|-------|-----------------|
| 1 | Instrument workflow-orchestrator to emit `playbook.executed` events | Platform team | Training data |
| 2 | Wire sp-intelligence to capture execution events in `incident_feedback` | Intelligence team | Training labels |
| 3 | Decide the ML problem: ranker (recommended) vs classifier | ML Architect (Moe) | Feature schema |
| 4 | Align feature schema across training pipeline, ONNX model, and inference | ML team | Model rebuild |
| 5 | Populate playbook catalog with real playbooks | SOC / Platform team | Candidate list |
| 6 | Accumulate 30+ days of real execution + feedback data | — | Minimum viable dataset |

### Estimated timeline to unblock

- Steps 1-2: ~1-2 sprints (instrumentation)
- Steps 3-5: ~1 sprint (design + catalog)
- Step 6: ~30-90 days of data accumulation after instrumentation
- **Earliest realistic model training: ~3-4 months from now**

This POC directory is preserved for when the prerequisites are met.
