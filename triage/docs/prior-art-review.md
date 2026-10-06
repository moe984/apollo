# Prior Art Review — Open-Source AI SOC Platforms

**Last Updated:** 2026-09-17
**Scope:** `beenuar/AiSOC` and `FunnyWolf/agentic-soc-platform`, assessed against our architecture.

---

## 1. The two projects

| | **beenuar/AiSOC** | **FunnyWolf/agentic-soc-platform** |
|---|---|---|
| Stars / forks | 2,395 / 263 | 1,184 / 212 |
| Created | 2026-05-02 | 2025-09-07 |
| Last pushed | 2026-09-14 | 2026-08-05 |
| License | MIT (declared in repo metadata) | README says MIT; **no license detected via API — verify** |
| Stack | Python, TypeScript, Go | Python (Django), TypeScript |
| Repo size | ~49 MB | ~28 MB |

Both are complete platforms, not libraries. Adopting either wholesale is a different decision from borrowing patterns.

### AiSOC in one line
> "Open-source AI-powered Security Operations Center — alert fusion, purple-team drills, agent-assisted triage, MITRE ATT&CK investigation."

Kafka event spine; storage across PostgreSQL, ClickHouse, OpenSearch, Qdrant, Neo4j, Redis. 83 connectors, 947 Sigma rules, OCSF normalisation, LangGraph orchestrator, Next.js console, FastAPI MCP server.

### Agentic SOC Platform in one line
> "Agents proactively participate in triage, investigation, enrichment, and knowledge accumulation so security teams can move from alert fatigue to AI-assisted decision-making."

Django backend with apps for `cases`, `alerts`, `artifacts`, `enrichments`, `knowledge`, `playbooks`, `agentic`, `agent_api`, `audit`. Splunk + ELK. Playbooks orchestrate SOAR actions and LLM analysis in one system.

---

## 2. What they confirm about our direction

**Neither uses a SQL federation engine or an off-the-shelf natural-language-to-SQL product.** Both reach Splunk through its **REST API**, wrap it in a purpose-built query layer, and correlate across sources in application code. That is independent corroboration of the shape we arrived at.

**"Federated search" in AiSOC is query *translation*, not query *federation*.** `services/connectors/app/federated/` translates one query into SPL, KQL, and ES|QL and fans out to connected SIEMs, merging results. It explicitly does **not** try to join SIEM telemetry with relational case data in a single query — same conclusion we reached: correlate in the orchestrator, not in the query engine.

This is also the same shape as Pythia's multi-platform strategy. Three independent projects converging on it is a strong signal.

---

## 3. What we should take from them

### 3.1 Investigation Ledger — adopt the richer spec (AiSOC)

We specified an evidence ledger. Theirs is more complete:

> "The Investigation Ledger stores the LLM prompt, the response, the evidence cited, and the downstream tool calls for every step of every run. Replays are available later."

Plus replayable decision narratives and **shareable redacted permalinks**. Storing the prompt and full tool-call chain — not just cited evidence — makes runs replayable and is what turns the ledger from an audit artifact into a debugging tool. Adopt.

### 3.2 Evaluation methodology — this closes an open unknown (AiSOC)

Our issue lists "evaluation methodology" as unresolved. AiSOC gates every PR with five suites:

- Alert reduction measured against a **fixed 1,000-alert stream**
- Rubric-based self-consistency across a **deterministic 200-incident dataset** (55 templates)
- Telemetry corpus validation
- Compose smoke tests (pre-commit + nightly cold-cache)
- End-to-end tests against seeded console data

Plus a public weekly scoreboard. The pattern worth copying: **a fixed alert stream for measuring reduction, and a deterministic incident set for measuring consistency.** That is a concrete, cheap answer to a question we had open.

### 3.3 Autonomy model — better than our binary (AiSOC)

We wrote "no autonomous containment in v1." Theirs is more useful:

> Auto-execute only for **reversible, low-blast-radius actions at high confidence** — a `confidence × blast-radius × reversibility` policy. Everything else is human-gated, with real rollback and post-action verification.

This lets you ship *some* automation safely rather than none. Recommend replacing our binary rule with this formulation, keeping v1 conservative by setting thresholds high.

### 3.4 Prompt-injection handling — a concrete pattern (AiSOC)

We flagged injection as a risk without a mitigation. Theirs:

> "a prompt-injection guard that demotes tampered evidence to manual review"

Demotion-to-manual rather than hard rejection is the right default for a SOC — you neither trust the content nor silently drop a potentially real signal.

### 3.5 SPL injection defence — working code to copy (Agentic SOC Platform)

`backend/integrations/siem/query_builders.py` validates Splunk index tokens against a strict regex and raises on anything else:

```python
_SPLUNK_INDEX_RE = re.compile(r"[a-zA-Z0-9_.:-]{1,80}")
# "Rejecting anything else prevents SPL injection through the
#  `search index=\"...\"` clause"
```

This directly validates our SPL-allowlist guardrail and gives us a tested implementation to model on. Note their comment names the exact injection vector.

### 3.6 Harness agent integration — relevant to how we work (Agentic SOC Platform)

> "Expose ASP capabilities to Claude Code / Codex / OpenCode and other Harness Agents through the CLI and plugins, enabling agents to operate Cases, search logs, query threat intelligence, and write modules and playbooks directly."

Worth studying for our own analyst-facing surface, given we already work this way.

### 3.7 Knowledge accumulation — our closed loop, built

> "Extract reusable knowledge from closed Case investigation records, response processes, and discussions."

This is our Phase 4 closed loop. Worth reviewing `backend/apps/knowledge/` before designing ours.

---

## 4. Where we should not follow

**AiSOC's operational footprint is very heavy for our stage.** Kafka + ClickHouse + OpenSearch + Qdrant + Neo4j + Redis + PostgreSQL is seven data systems before any investigation runs. Our design deliberately has one first-party store plus telemetry reached through an existing spoke. Do not adopt the storage tier; adopt the patterns.

**Neither solves our actual problem.** Both assume they own ingestion and normalise everything into their own store. We are not building a SIEM — telemetry stays in customer Splunk stacks, reached through Pythia, and our context plane is existing first-party data. Adopting either wholesale means re-platforming, not accelerating.

**Maturity caution.** AiSOC is ~4 months old with fast star growth; the metadata (2,395 stars, 263 forks) is plausible for a well-promoted project but the code has had little time in production. FunnyWolf's repo returns **no license via the API** despite the README claiming MIT — resolve that before copying any code.

---

## 5. Recommendation

Do not adopt either platform. Both are SIEM-shaped and assume ownership of ingestion and storage, which conflicts with our premise.

Do adopt five patterns, all cheap:

1. **Richer Investigation Ledger** — prompt + response + evidence + tool calls + replay
2. **Eval harness** — fixed alert stream for reduction, deterministic incident set for consistency, CI-gated
3. **`confidence × blast-radius × reversibility`** autonomy policy, replacing our binary containment rule
4. **Prompt-injection guard** that demotes tampered evidence to manual review
5. **SPL index-token validation** modelled on their `query_builders.py`

And run two reads before Phase 1: `backend/apps/knowledge/` for the closed loop, and AiSOC's `services/agents/` LangGraph orchestrator (~600 lines) as a data point for our undecided agent framework.

---

## Sources

- https://github.com/beenuar/AiSOC — README, `services/connectors/`, `services/agents/`, `services/mcp/`
- https://github.com/FunnyWolf/agentic-soc-platform — README, `backend/integrations/siem/`, `backend/apps/`
  (local clone already present at `poc/soc-ai-agent/agentic-soc-platform`, at commit `be3e9b4`)

All quoted text is from the projects' own documentation and source comments, treated as reference material only.
