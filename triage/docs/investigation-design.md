# Investigation Design — Borrowed Patterns

**Last Updated:** 2026-09-17
**Scope:** Concrete mechanics for the investigation piece, adapted from `beenuar/AiSOC` and `FunnyWolf/agentic-soc-platform`.
**Related:** [`prior-art-review.md`](./prior-art-review.md) · [`architecture-diagram.md`](./architecture-diagram.md)

Everything below is a specific, implementable pattern — not a platform adoption. Attribution is noted per item.

---

## 1. Structured investigation report (from Agentic SOC Platform)

Their `InvestigationReport` Pydantic schema is the shape our Report Writer should emit. Adapted:

```python
class EvidenceFinding(BaseModel):
    title: str
    finding_type: str
    subject: str        # the entity this is about
    evidence: str       # the raw evidence
    conclusion: str     # what it means

class InvestigationReport(BaseModel):
    verdict: str
    severity: str
    impact: str
    priority: str
    confidence: str
    digest: str                                    # one-line summary
    affected_assets:  list[AffectedAsset]
    evidence_findings: list[EvidenceFinding]
    attack_chain:     list[AttackChainStep]        # stage + description
    attack_timeline:  list[TimelineEvent]          # timestamp + behavior + evidence_field
    ioc_indicators:   list[IndicatorOfCompromise]
    remediations:     list[Remediation]            # action_type + description + priority
    unknowns:         list[str]
```

**The field worth stealing outright is `unknowns`.** It forces the agent to declare what it could not establish, which is the single cheapest defence against a confident-sounding but under-evidenced verdict. Our design said "cite evidence or the claim doesn't ship"; `unknowns` is the constructive other half — say what you couldn't determine rather than quietly omitting it.

`EvidenceFinding` separating `evidence` from `conclusion` is also worth keeping: it makes the inferential step explicit and separately reviewable.

---

## 2. Groundedness scoring (from AiSOC) — adopt as-is

This is the one to take first. It makes our "every claim cites its query" rule **mechanically enforceable, deterministically, with no LLM in the loop**:

> "what fraction of the concrete indicators an agent asserts in its output actually appear in the evidence it was given. An indicator in the output that is absent from the evidence is a hallucination."

Regex-extract concrete indicators (IPv4, SHA-256, MD5, CVE, MITRE technique IDs, domains) from both the output and the evidence bundle, then:

```
score = |claimed ∩ evidence| / |claimed|
hallucinated = claimed - evidence
```

Deliberate design choices worth preserving:
- **Only concrete, checkable indicators** — never prose. You cannot regex-check an argument, so don't pretend to.
- **No indicators claimed ⇒ score 1.0.** An agent that asserts no checkable fact cannot have hallucinated one.
- **Deterministic**, so it can gate CI without a model in the loop.

For us: run this over every Report Writer output against the evidence ledger's cited rows. Any `hallucinated` entry blocks the verdict from shipping and routes to manual review.

---

## 3. Ledger with input/output hashing (from AiSOC)

Our evidence ledger stored citations. Theirs fingerprints every step:

```python
class AuditEntry(BaseModel):
    id: UUID
    timestamp: datetime
    kind: StepKind
    agent: str
    summary: str
    input_hash:  str | None   # sha256 of serialised input
    output_hash: str | None   # sha256 of serialised output
    duration_ms: int
    metadata: dict
```

> "Used to fingerprint LLM prompts, tool calls, and evidence so the ledger can prove 'this exact input produced this exact output' without storing the raw blob inline."

That last clause is the insight: **hashes give you provable replay without the storage cost or the PII exposure of inlining every prompt.** Store the blob once, reference it by hash.

Their `StepKind` taxonomy is worth copying wholesale: `TOOL_CALL`, `LLM_PROMPT`, `LLM_RESPONSE`, `EVIDENCE_CITED`, `DECISION_REASON`, plus one per agent. `DECISION_REASON` as a first-class step type is good — it forces the reasoning to be logged as a discrete, queryable artifact rather than buried in prose.

---

## 4. Outcome priors — the closed loop, with its safety rules (from AiSOC)

Our Phase 4 "closed loop" was a sentence. They have the mechanism *and* the failure modes worked out:

> "Every triage outcome (autonomous OR human) is written back as a per-signature prior, so a later alert with the same evidence signature can be auto-suppressed instead of re-triaged. This is what makes alert volume actually shrink over time."

The four safety rules are the valuable part, and each one is a bug we would otherwise have shipped:

1. **AI priors are trusted less than human ones.** A human prior suppresses immediately; an AI prior needs corroboration — their defaults are `count >= 3` at `confidence >= 0.90`.
2. **Only auto-closeable dispositions ever suppress** (false positive / benign). 
3. **A prior true-positive never auto-closes a future alert.** Obvious in hindsight, catastrophic if missed.
4. **Human authorship is sticky** — an AI prior a human later overrode stays marked `human` and never downgrades.

Adopt all four. Rule 3 in particular is the difference between a loop that reduces noise and one that suppresses a real incident.

---

## 5. Confidence scoring discipline (from AiSOC)

Their design constraints are better than anything we specified:

1. **Pure functions — no I/O, no LLM calls.** The heuristic floor is what gates CI; an LLM-augmented score layers on top. This means confidence stays testable.
2. **Clamped to `[0.05, 0.95]`** — *"Never claim certainty; never claim impossibility."*
3. **Every score returns a `basis`** — a list of human-readable bullets, so the analyst sees *why*, not just *what*.

And it is calibration-gated in CI: **Brier score < 0.15 and ECE < 0.10** against their 200-incident corpus. That converts "is our confidence meaningful?" from a debate into a build failure.

Note their honest framing: the output "is not a classifier probability — it's a calibrated *evidence count* compressed into [0, 1]." We should be equally careful not to over-claim what the number means.

---

## 6. Multi-hypothesis debate step (from AiSOC)

Their `StepKind` includes:

> `DEBATE` — "a structured multi-hypothesis debate step (the replay UI renders it as a split-screen of competing hypotheses + their scores)"

This is a direct counter to premature convergence, which is the characteristic failure of a single-pass investigation agent: it latches onto the first plausible story and then gathers confirming evidence. Forcing competing hypotheses to be scored side by side — and making that visible in replay — is cheap and high-value. Worth adding to our Correlation agent.

---

## 7. Context field profiles (from Agentic SOC Platform)

They define explicit field allowlists controlling exactly which case fields are serialised into LLM context, per profile:

```python
AI_PROFILE_VERSION = "2026-06-20"
AI_FIELD_PROFILES = {
    "cases.Case": {
        "investigation": [...],   # fields for the investigation prompt
        "agent":         [...],   # fields for agent tool access
    }
}
```

Two benefits at once: **context discipline** (the prompt gets a curated set, not a serialised ORM object) and a **privacy boundary** (fields not on the list cannot reach a model). The profile is versioned, so a verdict can be attributed to the exact context shape that produced it.

---

## 8. Versioned prompt catalog (from Agentic SOC Platform)

Prompts are addressable IDs resolved through a catalog, with structured output bound to a schema:

```python
invoke_structured_llm(prompt_id=..., payload=..., output_schema=InvestigationReport)
```

Combined with `AI_PROFILE_VERSION`, this makes prompt changes diffable, reviewable, and attributable — a verdict records which prompt version and which context profile produced it. That is what makes regression analysis possible after a prompt change.

---

## 9. Agent decomposition — a different cut worth considering

| Ours | AiSOC |
|---|---|
| Triage, Enrichment, Pivot/Hunt, Correlation, Report Writer | Recon, Forensic, Responder, Reporter |

Theirs maps to incident-response phases; ours maps to data-access patterns. Theirs has a cleaner output contract — each agent emits a typed findings object (`ReconFindings`, `ForensicFindings`, `ResponderPlan`) rather than free text, and `ResponderPlan` carries `dry_run: bool = True` as a schema-level default.

**Recommendation:** keep our decomposition (it matches how our data is actually split) but adopt their **typed per-agent output contracts**. Every agent should return a validated Pydantic object, not prose. `ForensicFindings` carrying an explicit `blast_radius` and `root_cause_hypothesis` is a good template.

---

## 10. What to implement, in order

| # | Pattern | Effort | Why first |
|---|---|---|---|
| 1 | Groundedness scorer | Hours | Makes the honesty bar mechanical; gates CI immediately |
| 2 | `InvestigationReport` schema incl. `unknowns` | Hours | Defines the Report Writer contract |
| 3 | Typed per-agent output contracts | Days | Everything downstream depends on these |
| 4 | Ledger with input/output hashes | Days | Replay + provenance without blob cost |
| 5 | Confidence discipline (pure, clamped, `basis`) | Days | Testable from day one |
| 6 | Context field profiles | Days | Privacy boundary before any real data flows |
| 7 | Versioned prompt catalog | Days | Cheap now, expensive to retrofit |
| 8 | Outcome priors + the four safety rules | Weeks | Phase 4, but design the schema now |
| 9 | Multi-hypothesis debate step | Weeks | After single-pass works |

Items 1–3 are worth doing before Phase 1 ships, because they define contracts everything else is written against.

---

## Attribution

Patterns adapted from:
- `beenuar/AiSOC` (MIT) — `services/agents/app/{investigator,confidence,memory}/`
- `FunnyWolf/agentic-soc-platform` — `backend/apps/agentic/analysis/`; **license unconfirmed via API despite README claiming MIT — resolve before copying code verbatim.**

Quoted comments are from those projects' source. Nothing here is copied code; these are designs re-expressed for our architecture.
