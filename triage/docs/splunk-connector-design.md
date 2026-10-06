# Trino Splunk Connector — Design

> ## ⚠️ SUPERSEDED — 2026-09-17
>
> **This connector is cancelled, not deferred.** [Pythia](https://github.com/tekstream-cip/pythia) already performs this role in production — natural language to SPL grounded in a live per-tenant Splunk fingerprint, with parser and semantic validation, confidence scoring, and PII obfuscation — and does it better than a Trino connector could.
>
> This document is retained for its research value: the Starburst limitations (§1), the Splunk REST API and `tstats` findings (§2-§5), and the Trino SPI notes remain accurate and would matter if Trino returns when a second SQL source appears.
>
> See [`pythia-vs-trino-wren.md`](./pythia-vs-trino-wren.md) for the assessment and [`architecture-diagram.md`](./architecture-diagram.md) for the current architecture.


**Status:** Design proposal · **Date:** 2026-09-16
**Decision:** Build a native, open-source Trino connector for Splunk rather than licensing Starburst or settling for a second query plane.

---

## 1. Why build it

Re-reading the Starburst constraints, they are not *inherent to Splunk* — they are product choices:

| Starburst limitation | Is it inherent? |
|---|---|
| Saved reports only | **No.** The `search/jobs/export` REST endpoint accepts arbitrary SPL |
| 8 SQL types | **No.** Data model JSON declares real field types |
| Pushdown off by default (NULL bugs) | **No.** An implementation defect, not a Splunk constraint |
| Username/password on :8089 | **No.** Splunk supports JWT bearer tokens |
| Single-threaded report fetch | **No.** Time-sliced parallel export jobs are straightforward |

Splunk's REST API is fully capable of backing a real connector. Nobody has published an open-source one — there is no widely-adopted community implementation — which is an opportunity, not a warning.

**The payoff** is the thing that motivated the original architecture: genuine cross-source SQL joins between alert context and telemetry, in one engine, in one query.

```sql
SELECT a.alert_id, a.severity, ast.criticality, s.failure_count
FROM   postgres.soc.alerts        a
JOIN   postgres.soc.assets        ast ON ast.id = a.asset_id
JOIN   splunk.datamodel.authentication s
       ON s.user = a.username
      AND s."_time" BETWEEN a.first_seen - INTERVAL '1' HOUR
                        AND a.last_seen  + INTERVAL '1' HOUR
WHERE  a.status = 'open' AND ast.criticality = 'high';
```

With this, Wren AI's MDL can model Splunk data models as first-class entities with approved joins to Postgres entities, and the agent gets **one tool, one dialect, one semantic layer** instead of an orchestrator juggling two planes.

---

## 2. Catalog design — four schemas, not one

This is where we beat Starburst. Splunk is schema-on-read, so the right move is to expose *several* table abstractions with different tradeoffs and let the query decide.

```
splunk.
├── datamodel.*      CIM data models → typed, accelerated, FAST      ← primary surface
├── index.*          raw indexes → generic schema, flexible, slower
├── savedsearch.*    saved searches → curated views (Starburst parity)
└── system.query()   raw SPL passthrough via table function          ← escape hatch
```

### 2.1 `splunk.datamodel.*` — the crown jewel

CIM data models (`Authentication`, `Network_Traffic`, `Web`, `Endpoint.Processes`, `Malware`, …) are **already schematized** and, when accelerated, backed by `.tsidx` summary files. `| tstats summariesonly=t FROM datamodel=X.Y` reads those summaries instead of scanning raw events — the documented order-of-magnitude speedup, and the same mechanism Splunk Enterprise Security relies on.

- **Discovery:** `GET /servicesNS/-/-/datamodel/model` returns JSON per model; the `description` field carries the dataset/field definitions including declared types (`string`, `number`, `boolean`, `timestamp`, `ipv4`).
- **Mapping:** one Trino table per dataset. `Authentication.user` → column `user`. Declared types → real SQL types.
- **Execution:** `| tstats summariesonly=t <aggs> FROM datamodel=Authentication WHERE <pushed predicates> BY <group cols>`

For a SOC this is exactly the right abstraction: CIM *is* the security ontology, already normalized across vendors. It maps onto Wren's MDL almost one-to-one.

> ⚠️ `summariesonly=t` returns **only** accelerated data. Make it a per-table/session property, defaulting to `true` for speed but overridable for completeness. Getting this wrong means silently missing events — unacceptable in an investigation. Surface which mode a query ran in.

### 2.2 `splunk.index.*` — the fallback

One table per index, fixed generic schema:

| Column | Type |
|---|---|
| `_time` | `TIMESTAMP(3) WITH TIME ZONE` |
| `host`, `source`, `sourcetype`, `index` | `VARCHAR` |
| `_raw` | `VARCHAR` |
| `fields` | `MAP(VARCHAR, VARCHAR)` — all other extracted fields |

Optional per-index typed column overrides in catalog config, so known sourcetypes get real columns. Executes as plain `search index=X ...`.

### 2.3 `splunk.savedsearch.*` — parity

Saved searches as tables, discovered via `/services/saved/searches`. Cheap to implement, and it gives the governance story ("only these approved searches") to teams that want it — as an *option*, not the only mode.

### 2.4 `splunk.system.query()` — the escape hatch

Trino's polymorphic table functions (`ConnectorTableFunction` + `AbstractConnectorTableFunction`) support exactly this pattern. Verified against the SPI docs: scalar arguments, `GENERIC_TABLE` return type, `analyze()` returns the row type plus a `ConnectorTableFunctionHandle`, and the connector supplies a `ConnectorSplitSource` for distributed execution.

```sql
SELECT * FROM TABLE(splunk.system.query(
    query    => '| tstats count FROM datamodel=Authentication
                 WHERE Authentication.action=failure BY Authentication.user, _time span=1h',
    earliest => '-24h',
    latest   => 'now'
));
```

This kills the biggest objection to SQL-over-Splunk: SPL has expressive commands (`transaction`, `streamstats`, `map`, `eventstats`) that do not translate to SQL, and analysts already think in SPL. The PTF means we never have to say "SQL can't express that" — full SPL is always one function call away, and its result still joins to Postgres as a normal relation.

Schema for a PTF result must be resolved at analysis time — either run the search with `| head 0` to get the field list, or require an explicit `columns => descriptor(...)` argument. **Prefer the explicit descriptor** for agent use: deterministic, no analysis-time search cost, no surprise schema drift between planning and execution.

---

## 3. Pushdown — where the performance lives

Implement `ConnectorMetadata` optimization hooks. Per the SPI contract, **return `Optional.empty()` when a call changes nothing**, even for pushdowns generally supported, or the optimizer loops.

| Hook | SPL translation | Priority |
|---|---|---|
| `applyFilter` — `_time` range | → `earliest_time` / `latest_time` **job parameters** | 🔴 **Critical** |
| `applyFilter` — `index`/`host`/`sourcetype`/`source` equality & IN | → base search terms (`index=web OR index=proxy`) | 🔴 Critical |
| `applyFilter` — other predicates | → `| search f="v"` / `| where` | 🟡 High |
| `applyProjection` | → `| fields f1, f2` | 🔴 Critical (wire volume) |
| `applyAggregation` — COUNT/SUM/AVG/MIN/MAX + GROUP BY | → `| tstats <agg> ... BY <cols>` | 🔴 **Critical** |
| `applyLimit` | → `| head N` | 🟡 High |
| `applyTopN` | → `| sort N -field` | 🟢 Medium |

**The two that matter most:**

1. **Time-range pushdown.** Splunk's entire performance model is time-bucketed. An unbounded search is a full index scan. This must be non-optional — see §6.
2. **Aggregation pushdown to `tstats`.** `SELECT user, count(*) ... GROUP BY user` over 90 days is the difference between streaming a billion rows to Trino and Splunk returning a few thousand pre-aggregated rows from tsidx. For SOC analytics — which is overwhelmingly counting and grouping — this *is* the connector's value.

Store pushed-down state in the `ConnectorTableHandle` so the split manager and page source can reconstruct the final SPL.

---

## 4. Parallelism — time-sliced splits

A single Splunk search job is executed by the search head; you cannot shard *within* a job. Parallelize *across* jobs by **time-slicing**:

```
[earliest, latest]  →  N windows  →  N concurrent export jobs  →  N Trino splits
```

`ConnectorSplitManager` divides the (pushed-down) time range into windows, each becoming a `ConnectorSplit` carrying `{spl, earliest, latest}`. Splunk time ranges are half-open (`earliest <= _time < latest`), so windows tile cleanly — **no duplicates, no gaps**. Verify this property in tests; it is the correctness foundation of the whole design.

Window sizing should be adaptive, not fixed: use `/services/search/jobs` metadata or a cheap `| tstats count by _time span=` probe to estimate density, then target roughly equal rows per split. Fixed windows produce badly skewed splits on bursty security telemetry (quiet nights, loud mornings).

> 🔴 **This is also the connector's biggest operational risk.** Splunk enforces per-role concurrency via `srchJobsQuota` in `authorize.conf` (default 100) and `base_max_searches` in `limits.conf`. A Trino query fanning out 200 splits will exhaust the quota and **degrade the search head for every other user, including live analysts.** Mitigations, all mandatory:
> - `splunk.max-concurrent-searches` catalog property, defaulting **low** (4–8)
> - A dedicated Splunk role for the connector with its own quota, so blast radius is contained
> - Queue splits against a semaphore rather than letting Trino's scheduler dictate fan-out
> - Exponential backoff on quota-exceeded responses

---

## 5. Transport

`POST /services/search/jobs/export` with `output_mode=json`, consumed as a **stream**. This is the documented path for large result sets — it streams results over the wire rather than materializing them on the search head and polling `/results`. Avoids the auto-finalize behaviour reported on very large `jobs/results` fetches.

- **Auth:** `Authorization: Bearer <token>`. Splunk auth tokens are JWTs — better than Starburst's username/password, and revocable independently.
- **Page source:** implement `ConnectorPageSourceProvider` (not `RecordSet`) and build Trino `Page`s directly from the streamed JSON for lower per-row overhead.
- **Backpressure:** stream-parse; never buffer a full result set in worker heap.
- **Cancellation:** on Trino query cancel, `DELETE` the Splunk job so it does not keep burning search-head CPU. Easy to forget; leaks quota fast.

---

## 6. Guardrails

Agent-generated SQL against a production search head demands hard limits **in the connector**, not in prompts:

1. **Mandatory time bound.** Reject or auto-clamp any query without a `_time` predicate. `splunk.max-time-range` (e.g. 30d) and `splunk.default-time-range` (e.g. 24h). Non-negotiable.
2. **Concurrency semaphore** (§4).
3. **Row cap** per query with a clear error, not silent truncation. Silent truncation in an investigation produces confidently wrong conclusions.
4. **Read-only.** No write SPI, no `| delete`, no `| outputlookup`. Reject any PTF payload containing generating/mutating commands via allowlist, not denylist.
5. **PTF input is untrusted.** The `query()` argument is an SPL injection surface when an LLM composes it. Allowlist permitted leading commands (`search`, `tstats`, `from`, `datamodel`), forbid `| script`, `| runshell`, `| sendemail`, `| outputlookup`, `| delete`, `| collect`.
6. **Per-role Splunk credentials** so Splunk's own RBAC still applies — the connector must not be a privilege-escalation path around index-level permissions.

---

## 7. Build plan

**Stack:** Java 23+, Maven, `trino-spi` (`provided` scope), `trino-plugin` packaging. Reference implementations in `trinodb/trino` — the `jmx`, `prometheus`, and `elasticsearch` connectors are the closest analogues (Elasticsearch especially: schema-on-read, REST transport, similar pushdown problems).

| Milestone | Scope | Est. |
|---|---|---|
| **M1 — Walking skeleton** | `Plugin`, `ConnectorFactory`, `Connector`, `ConnectorMetadata`. One hardcoded index table. Single split, no pushdown. `SELECT * FROM splunk.index.main LIMIT 10` works. | 1–2 wk |
| **M2 — Real transport & types** | Streaming export page source, JWT auth, TLS, `_time` typing, `fields` map. | 1–2 wk |
| **M3 — Pushdown** | `applyFilter` (time + indexed fields), `applyProjection`, `applyLimit`. | 2 wk |
| **M4 — Data models** | Discovery via `/datamodel/model`, typed columns, `tstats` execution, `summariesonly` control. | 2 wk |
| **M5 — Parallelism** | Time-sliced `ConnectorSplitManager`, adaptive windows, concurrency semaphore, job cancellation. | 2 wk |
| **M6 — Aggregation pushdown** | `applyAggregation` → `tstats`. Highest performance payoff. | 2–3 wk |
| **M7 — PTF + saved searches** | `system.query()` table function, `savedsearch` schema, SPL allowlist. | 2 wk |
| **M8 — Hardening** | Guardrails, correctness test suite (esp. split tiling), docs, OSS release. | 2 wk |

**Realistic total: ~3–4 months** for one experienced Java engineer to production quality; a usable POC lands at **M3, ~5–6 weeks**.

**Ongoing tax to budget for, honestly:** Trino releases roughly monthly and **the SPI is not stable across versions**. Expect recurring maintenance to track SPI changes — this is the main reason third-party connectors bit-rot. Pin a Trino version, and treat connector upgrades as planned work, not incidental.

---

## 8. Revised recommendation

The connector changes the architecture from two planes to one:

| | Original plan | With the connector |
|---|---|---|
| Splunk access | Splunk MCP (separate SPL plane) | Trino catalog — SQL, plus SPL via PTF |
| Cross-source joins | In agent code, manually | Real SQL joins in Trino |
| Semantic layer coverage | Postgres only | Postgres **and** Splunk in one MDL |
| Agent tools | 2 query tools + reconciliation logic | 1 query tool |
| Governance | Split across two systems | One place: Trino roles + MDL |
| Build cost | ~0 | ~3–4 months + maintenance |

**Recommended: build it, and keep Splunk MCP as the fallback during M1–M5.** They are complementary, not competing. The MCP server unblocks agent development *this week* while the connector is built, then becomes the escape hatch for the genuinely SPL-shaped work the PTF doesn't cover. Nothing is wasted, and Phase 1 of the agent roadmap is not blocked on a 3-month dependency.

This also means the security-lake work (original Phase 3) drops in priority. The connector gives cross-source SQL without an ETL pipeline. Iceberg becomes a *cost* play — moving cold telemetry off Splunk licensing — rather than a prerequisite for the architecture.

**Open-sourcing this is worth considering.** There is no OSS Trino Splunk connector and clear demand for one. It would be the reference implementation.

---

## 9. Spikes to run before committing

These are the assumptions that, if wrong, change the plan. Time-box to ~1 week total.

1. **`tstats` field fidelity.** Do the accelerated data models in *your* Splunk actually carry the fields investigations need, and is acceleration enabled and backfilled? If CIM compliance is poor, §2.1's value drops sharply and `index.*` becomes the primary surface.
2. **Export throughput.** Measure sustained rows/sec from `jobs/export` on a realistic query. Sets split sizing and tells you whether the whole approach is viable at your data volume.
3. **Split tiling correctness.** Confirm half-open time semantics empirically — run N windows vs. one whole-range query and assert identical row counts. **This is the single most important correctness test.**
4. **Concurrency headroom.** What is the connector role's `srchJobsQuota`, and what fan-out can the search head absorb without hurting analysts? Determines `max-concurrent-searches`.
5. **SPI churn.** Diff the connector SPI across the last 3–4 Trino releases to size the real maintenance tax.
6. **Datamodel JSON parsing.** Pull `/servicesNS/-/-/datamodel/model` from the real instance and confirm the type declarations are rich and consistent enough to auto-generate schemas.

---

## Sources

- [Trino — writing connectors (SPI)](https://trino.io/docs/current/develop/connectors.html) · [table functions](https://trino.io/docs/current/develop/table-functions.html) · [plugins](https://trino.io/docs/current/installation/plugins.html) · [connector index](https://trino.io/docs/current/connector.html)
- [Starburst Splunk connector](https://docs.starburst.io/latest/connector/starburst-splunk.html) (the limitations we're improving on)
- [Splunk — export data using the REST API](https://help.splunk.com/en/splunk-enterprise/search/search-manual/10.2/export-search-results/export-data-using-the-splunk-rest-api) · [creating searches using the REST API](https://help.splunk.com/en/splunk-enterprise/leverage-rest-apis/rest-api-tutorials/9.4/rest-api-tutorials/creating-searches-using-the-rest-api) · [REST endpoint list](https://docs.splunk.com/Documentation/Splunk/latest/RESTREF/RESTlist) · [knowledge endpoints](https://docs.splunk.com/Documentation/SplunkCloud/latest/RESTREF/RESTknowledge)
- [Splunk — tstats](https://help.splunk.com/en/splunk-enterprise/search/spl-search-reference/10.4/search-commands/tstats) · [accelerate data models](https://help.splunk.com/en/splunk-enterprise/manage-knowledge-objects/knowledge-management-manual/9.4/use-data-summaries-to-accelerate-searches/accelerate-data-models) · [CIM Endpoint data model](https://help.splunk.com/en/splunk-cloud-platform/common-information-model/6.1/data-models/endpoint) · [data models used by ES](https://dev.splunk.com/enterprise/docs/devtools/enterprisesecurity/datamodelsusedbyes)
- [Splunk — authentication tokens](https://help.splunk.com/en/splunk-enterprise/administer/manage-users-and-security/10.0/authenticate-into-the-splunk-platform-with-tokens/use-authentication-tokens) · [search concurrency](https://help.splunk.com/en/splunk-enterprise/administer/distributed-search/9.4/manage-search-head-clustering/control-search-concurrency-on-search-head-clusters) · [authorize.conf](https://help.splunk.com/en?resourceId=Splunk_Admin_Authorizeconf)
- [Custom Trino connector walkthrough](https://techjogging.com/get-started-custom-connector-plugin-trino.html) · [resurfaceio/trino-connector](https://github.com/resurfaceio/trino-connector) · [Radico/trino-plugins](https://github.com/Radico/trino-plugins)
