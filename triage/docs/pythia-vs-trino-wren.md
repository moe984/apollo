# Can Pythia replace Trino + Wren AI?

**Last Updated:** 2026-09-17
**Status:** Assessment
**Verdict:** **Yes for the Splunk half — and it makes the 3–4 month Splunk connector build redundant.** No for cross-source joins, which is a much narrower gap than the original design assumed.

---

## 1. What Pythia actually is

From `Projects/pythia` (README, `src/`, `docs/design/`, `mcp/`):

> "AI-powered SPL generation and search federation for Managed Detection and Response."

It continuously probes each customer's Splunk (indexes, sourcetypes, fields, CIM mappings) and uses that **live fingerprint** as query-time context, so generated SPL references things that actually exist. Then it validates, scores, obfuscates, and executes.

```
Per-customer Splunk instances
  └── Metadata Collector (scheduled REST + SPL probes)
        └── PostgreSQL (structured per-customer fingerprint)
              ├── Query API (FastAPI)
              │     ├── ChromaDB (SPL docs vector store)
              │     ├── Claude API (LLM generation)
              │     ├── Parser validator (Splunk service.parse())
              │     ├── Semantic validator (dry-run)
              │     └── Confidence scorer (0.0–1.0)
              └── Federation layer (parallel execution → MoM)
```

This is not a prototype. It is deployed behind an ALB, has service principals with capability-based RBAC, rate limits, circuit breakers, cost tracking, an eval harness, and a **verified MCP server** (`mcp/README.md`, verified 2026-07-31 against principal `jon-mcp`). CIP's own `cip-apollo` service principal already calls it on a cron.

---

## 2. Component-by-component

Almost every piece I specified as "to build" already exists in Pythia:

| Proposed component | Pythia equivalent | Status |
|---|---|---|
| **Splunk Trino connector** (M1–M8, ~3–4 months) | `splunk_client.py` + full SPL generation pipeline | ✅ **Built, in production** |
| Wren AI NL→query | `translate_pipeline.py`, `prompt.py`, `llm.py` | ✅ Built |
| MDL semantic layer (hand-authored YAML) | Live per-customer fingerprint: `indexes`, `sourcetypes`, `fields`, `cim_mappings`, `data_models`, `macros`, `lookups`, `eventtypes`, `tags` tables, continuously refreshed by `metadata_collector.py` | ✅ Built — and **live-probed rather than hand-maintained** |
| Dry-Plan Validator | `parser.py` (Splunk `service.parse()`) + semantic dry-run + `spl_lint.py`, `spl_syntax.py` | ✅ Built |
| Golden Query Suite | eval harness + `search_scorer.py`, `score_baselines.py`, `pct_score.py`, 933K-search fleet corpus | ✅ Built |
| SPL Command Allowlist | `capabilities.py`, `admission.py`, consent gates (e.g. `tsmultisearch_enabled`) | ✅ Built |
| Query Budget Enforcer | `rate_limit.py`, `probe_throttle.py`, `circuit_breaker.py`, `capacity.py`, per-principal `rate_per_hour` | ✅ Built |
| **LLM data governance** (my open question) | `obfuscator.py`, `ip_anonymizer.py`, `raw_scrubber.py`, `url_defanger.py`, `obfuscation_confidence.py` | ✅ **Already solved** |
| Splunk MCP Server (interim plane) | Pythia MCP server — tools enumerated at runtime from `GET /api/agent/tools`, capability-filtered | ✅ Built |
| Agent tool registry | `agent_tools.py` | ✅ Built |
| ATT&CK mapping | `mitre.py`, `mitre_coverage.py`, `threat_hunting_ttp.py` | ✅ Built |

Current MCP tools (Phase B): `get_data_source_presence`, `write_spl`, `check_value_presence`, `run_search`. New tools added to `agent_tools.py` appear automatically with no client change.

---

## 3. What Pythia does *not* do

Three real gaps, and only the first matters much:

**1. It cannot query PostgreSQL as an investigation data source.** Its Postgres holds the *Splunk fingerprint* — `customers`, `indexes`, `sourcetypes`, `fields`, `value_index`, `cim_mappings`, `search_history`, `saved_searches`. There is no `alerts`, `cases`, or `assets` table. Our alert context (dedup_v2 output, cases, asset criticality, detection metadata) is outside its world.

**2. It emits SPL, not SQL.** So there is no single query language spanning telemetry and alert context. Multi-platform work is underway (`siem_connectors/connectors/` has `splunk.py`, `sentinel.py`, `mcp.py`; epics filed for KQL, CQL, XQL, Chronicle, Elastic) — but that is *more SIEMs*, not relational sources.

**3. Its "federation" is horizontal, not vertical.** `fan_out_resolver.py` / `search_cohort.py` run the same question across many customer Splunk stacks and normalize at a manager-of-managers. That is fan-out across tenants, not joining heterogeneous sources.

---

## 4. What this does to the architecture

The original design justified Trino as "one SQL plane over Postgres + Splunk," and justified a 3–4 month connector build to drag Splunk into that plane. Pythia removes the reason for both.

**The Splunk connector should not be built.** Beyond being redundant, a Trino connector would be *worse* than Pythia for this job:

| | Trino Splunk connector | Pythia |
|---|---|---|
| Environment grounding | None — static catalog | Live per-tenant fingerprint |
| Query language | SQL (loses SPL expressiveness) | Native SPL/SPL2 |
| Validation | `dry_plan` only | Parser + semantic dry-run + confidence score |
| PII handling | None | Obfuscation pipeline before LLM |
| Effort | ~3–4 months + SPI maintenance | Already deployed |

**And Trino itself may not be needed yet.** Trino earns its place by federating *many* sources. If Splunk is reached through Pythia, the only remaining SQL source is one PostgreSQL. Querying Postgres directly is simpler than standing up a coordinator and workers to query one database.

Likewise **Wren AI becomes optional**. Its value was a governed semantic layer over a heterogeneous federation. Over a single well-known Postgres schema, NL→SQL is a much smaller problem — though Wren's MDL still has real value as the *investigation ontology* (approved joins, tribal knowledge) if we want that governance.

### Revised shape

```
Orchestrator
  ├── Pythia MCP        → telemetry (SPL, any customer Splunk stack)
  └── Postgres (direct) → alert context, cases, assets, detections
        [+ Wren AI if we want a governed ontology over it]
        [+ Trino only when a 2nd SQL source appears — e.g. Iceberg]
```

Cross-source correlation moves from the query layer into the orchestrator: fetch context from Postgres, fetch evidence via Pythia, join in agent code. That is a real loss — it was the elegant part of the original design — but it costs orchestration logic, not three months of connector engineering.

---

## 5. Risks and things to verify

| Item | Why it matters |
|---|---|
| **Tenancy model fit** | Pythia is built around MDR `stack_id` per customer. Confirm it serves a single-tenant internal SOC use case without contortion. |
| **Rate limit as the real ceiling** | Per `mcp/README.md`: a principal's `rate_per_hour` "is the real ceiling on how many customer-Splunk searches the agent can run." An investigation agent is far burstier than a human. Size this deliberately. |
| **Throughput history** | `pythia_prod_architecture.md` documents CIP's `cip-apollo` cron causing 504s — load 22 on 8 vCPUs, single EC2. Confirm the Prod ECS transpose landed before pointing agents at it. |
| **Tool surface gaps** | Four tools today. Investigation may need more (e.g. time-series pivots, entity expansion). Adding them is a server-side change to `agent_tools.py`, not a client change. |
| **Ownership** | Pythia is Jon's. Agent-driven load and new tools are a cross-team dependency, not something we control. |
| **Postgres NL→SQL still unsolved** | Pythia does nothing here. Wren AI or direct SQL still needed for the context plane. |

---

## 6. Recommendation

1. **Cancel the Splunk connector build.** Fold the design doc into an appendix — the research stands, the decision is reversed.
2. **Spike Pythia MCP against a real investigation question** this week. It is already verified working; the question is fit, not function.
3. **Defer Trino** until a second SQL source exists. Do not stand up a coordinator to query one Postgres.
4. **Re-scope Wren AI** to the Postgres context plane only, and decide whether the MDL governance is worth it at that reduced scope.
5. **Talk to Jon** about tenancy, rate limits, and the Prod transpose before committing.

The original research was not wasted — the Starburst and Trino findings are still correct, and the guardrail/evidence-ledger design carries over unchanged. What changed is that the expensive part turned out to already exist, two repos over.

---

## Sources

All paths relative to `Projects/pythia`:
`README.md` · `docs/design/pythia_prod_architecture.md` · `docs/design/multi_platform_language_strategy.md` · `mcp/README.md` · `src/models.py` · `src/services/agent_tools.py` · `src/services/` (metadata_collector, translate_pipeline, parser, obfuscator, confidence, fan_out_resolver, siem_connectors)
