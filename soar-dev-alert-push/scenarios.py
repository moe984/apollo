#!/usr/bin/env python3
"""
SOAR Gate Scenario Runner

Exercises the HTTP response taxonomy of Apollo's SOAR push gate
(POST /api/v1/ingest/soar) against a target environment.

Companion to replay.py (which replays real SOAR containers). This script
uses synthetic containers shaped like replay.py's payloads, but targets
the current gate with x-api-key auth (replay.py's HMAC auth to
/api/v1/alerts/webhook is legacy; removed in apollo#537).

Usage:
    # Unauthenticated scenarios only (401s, 400, 413, ordering)
    python scenarios.py

    # Full suite (needs a valid key; soar_gateway scope for 422 scenarios)
    python scenarios.py --api-key <key> --client-name <slug>

    # Target override
    python scenarios.py --base-url https://apollo-dev.tekstreampoc.com

Each scenario logs to its OWN file (logs/<UTC timestamp>/<scenario-id>.log)
carrying a description of what the scenario tests, the full request, and its
response together. The run summary table is written to summary.log in the
same directory.
"""
import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    print("requests not installed. Run: pip install requests")
    sys.exit(1)

BASE_URL_DEFAULT = "https://apollo-dev.tekstreampoc.com"
GATE_PATH = "/api/v1/ingest/soar"
RESOLVE_PATH = "/api/v1/ingest/soar/resolve"
RULE = "=" * 78
THIN = "-" * 78


def synthetic_container(container_id, sdi, name="Scenario synthetic notable",
                        severity="medium", tenant_name=None):
    c = {
        "id": container_id,
        "source_data_identifier": sdi,
        "name": name,
        "severity": severity,
        "label": "events",
        "owner_name": "automation",
    }
    # Embed an event-level tenant signal (read by the gateway resolver ahead
    # of the X-Client-Name header). Used by the mismatch scenario to create a
    # genuine event-vs-header conflict.
    if tenant_name is not None:
        c["tenant_name"] = tenant_name
    return c


# CEF soar_instance stamped on every artifact (--soar-instance). Apollo
# attributes an alert's Source from this value, matched to the connection
# whose SOAR instance it is; empty leaves the alert as Unknown SOAR.
SOAR_INSTANCE = None


def synthetic_artifacts(src_ip="10.66.0.14", user="scenario.user"):
    artifacts = [
        {
            "id": 1,
            "label": "event",
            "cef": {
                "sourceAddress": src_ip,
                "src_ip": src_ip,
                "sourceUserName": user,
                "user": user,
                "destinationHostName": "wks-scenario-01",
                "dest_host": "wks-scenario-01",
            },
        }
    ]
    if SOAR_INSTANCE:
        for a in artifacts:
            a["cef"]["soar_instance"] = SOAR_INSTANCE
    return artifacts


def gate_body(container_id, sdi, tenant_name=None, **kw):
    return {
        "container": synthetic_container(container_id, sdi, tenant_name=tenant_name, **kw),
        "artifacts": synthetic_artifacts(),
    }


class Runner:
    def __init__(self, base_url, api_key, client_name, log_dir, timeout):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.client_name = client_name
        self.log_dir = log_dir
        self.timeout = timeout
        self.session = requests.Session()
        self.results = []
        self.run_header = "\n".join([
            RULE,
            "SOAR GATE SCENARIO",
            f"Target:  {self.base_url}",
            f"Auth:    {'x-api-key provided' if api_key else 'unauthenticated subset only'}",
            RULE,
        ])

    def _log(self, scenario_id, text):
        with open(os.path.join(self.log_dir, f"{scenario_id}.log"), "w") as f:
            f.write(self.run_header + text)

    def run(self, scenario_id, title, description, expect, method, path, *,
            headers=None, json_body=None, raw_body=None, params=None,
            skip_reason=None):
        started = datetime.now(timezone.utc).isoformat()
        block = [
            "",
            RULE,
            f"SCENARIO {scenario_id}: {title}",
            RULE,
            f"Started:  {started}",
            f"Expected: HTTP {expect}",
            "",
            "WHAT THIS SCENARIO TESTS",
            description.strip(),
            "",
        ]

        if skip_reason:
            block += [f"SKIPPED: {skip_reason}", ""]
            self._log(scenario_id, "\n".join(block))
            self.results.append({"scenario": scenario_id, "title": title,
                                 "expected": expect, "verdict": "SKIPPED",
                                 "got": "-", "corr": "-"})
            print(f"  [{scenario_id}] SKIP: {skip_reason}")
            return None

        url = f"{self.base_url}{path}"
        hdrs = dict(headers or {})
        shown_hdrs = {k: ("<redacted>" if k.lower() == "x-api-key" else v)
                      for k, v in hdrs.items()}

        block += [THIN, "REQUEST", THIN,
                  f"{method} {url}" + (f"  params={params}" if params else "")]
        if shown_hdrs:
            block.append("Headers:")
            block += [f"  {k}: {v}" for k, v in shown_hdrs.items()]
        if json_body is not None:
            block += ["Body:", json.dumps(json_body, indent=2)]
        elif raw_body is not None:
            preview = raw_body[:300] if isinstance(raw_body, str) else repr(raw_body[:300])
            block += [f"Body: raw, {len(raw_body)} bytes, preview:", preview
                      + ("..." if len(raw_body) > 300 else "")]

        t0 = time.monotonic()
        try:
            resp = self.session.request(
                method, url, headers=hdrs, params=params,
                json=json_body if json_body is not None else None,
                data=raw_body if raw_body is not None else None,
                timeout=self.timeout,
            )
            elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
            try:
                body = resp.json()
                body_text = json.dumps(body, indent=2)
            except ValueError:
                body = None
                body_text = resp.text[:2000]
            corr = (body or {}).get("correlationId") if isinstance(body, dict) else None
            got = resp.status_code
            verdict = "MATCH" if str(got) in str(expect) else "UNEXPECTED"

            block += ["", THIN, f"RESPONSE  (HTTP {got}, {elapsed_ms} ms)", THIN,
                      "Headers:"]
            block += [f"  {k}: {v}" for k, v in resp.headers.items()
                      if k.lower() in ("content-type", "x-correlation-id",
                                       "retry-after", "x-ratelimit-limit",
                                       "x-ratelimit-remaining", "x-ratelimit-reset",
                                       "content-length")]
            block += ["Body:", body_text, "",
                      f"VERDICT: {verdict} (expected {expect}, got {got})"
                      + (f"   correlationId={corr}" if corr else ""), ""]
            print(f"  [{scenario_id}] {got} ({elapsed_ms}ms) expected {expect} -> {verdict}"
                  + (f" corr={corr}" if corr else ""))
            self.results.append({"scenario": scenario_id, "title": title,
                                 "expected": expect, "got": got,
                                 "verdict": verdict, "corr": corr or "-"})
        except requests.RequestException as e:
            block += ["", THIN, "RESPONSE", THIN, f"TRANSPORT ERROR: {e}", ""]
            resp = None
            print(f"  [{scenario_id}] TRANSPORT ERROR: {e}")
            self.results.append({"scenario": scenario_id, "title": title,
                                 "expected": expect, "got": "ERR",
                                 "verdict": "TRANSPORT_ERROR", "corr": "-"})

        self._log(scenario_id, "\n".join(block))
        return resp

    def summary(self):
        lines = ["", RULE, "RUN SUMMARY", RULE,
                 f"Finished: {datetime.now(timezone.utc).isoformat()}", "",
                 f"{'Scenario':<28} {'Expected':<9} {'Got':<5} {'Verdict':<16} Correlation ID"]
        for r in self.results:
            lines.append(f"{r['scenario']:<28} {str(r['expected']):<9} "
                         f"{str(r['got']):<5} {r['verdict']:<16} {r['corr']}")
        text = "\n".join(lines) + "\n"
        with open(os.path.join(self.log_dir, "summary.log"), "w") as f:
            f.write(text)
        print(text)


def main():
    p = argparse.ArgumentParser(description="Run SOAR gate HTTP taxonomy scenarios")
    p.add_argument("--base-url", default=os.environ.get("APOLLO_BASE_URL", BASE_URL_DEFAULT))
    p.add_argument("--api-key", default=os.environ.get("APOLLO_API_KEY"))
    p.add_argument("--client-name", default=os.environ.get("APOLLO_CLIENT_NAME"),
                   help="Known-good client slug/name for tenant resolution scenarios")
    p.add_argument("--soar-instance", default=os.environ.get("APOLLO_SOAR_INSTANCE"),
                   help="CEF soar_instance to stamp on every artifact (e.g. SOARDEV)")
    p.add_argument("--timeout", type=float, default=30.0)
    args = p.parse_args()
    global SOAR_INSTANCE
    SOAR_INSTANCE = args.soar_instance

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", stamp)
    os.makedirs(log_dir, exist_ok=True)

    r = Runner(args.base_url, args.api_key, args.client_name, log_dir, args.timeout)
    run_tag = uuid.uuid4().hex[:8]
    cid_base = int(time.time()) % 100_000_000

    print(f"Target: {r.base_url}   Logs: {log_dir}\n")
    key_hdr = {"x-api-key": args.api_key} if args.api_key else {}
    cn_hdr = {"X-Client-Name": args.client_name} if args.client_name else {}

    # ---- Unauthenticated / pre-auth lifecycle scenarios ----
    r.run(
        "S05-400-missing-sdi", "Body missing source_data_identifier",
        """A syntactically valid JSON body whose container is missing
source_data_identifier (the dedup join key, one of only two hard-required
fields). Verifies the gate rejects it with 400 from schema validation, and
that validation runs BEFORE auth in the Fastify lifecycle: no x-api-key is
sent, so a 400 (not 401) proves request-shape checks precede the auth
preHandler. Error path: parseSoarGateBody / zod schema -> global error
handler (error-handler.ts).""",
        400, "POST", GATE_PATH,
        json_body={"container": {"id": cid_base + 3, "name": "no sdi"}, "artifacts": []})

    r.run(
        "S06-400-bad-container-id", "container.id non-numeric",
        """container.id is a string instead of the required number. Same 400
validation branch as S05 but exercising the type check on the second
hard-required field. SOAR playbook authors sending a stringified id should
get a clear 400, not a silent failure.""",
        400, "POST", GATE_PATH,
        json_body={"container": {"id": "not-a-number",
                                 "source_data_identifier": f"scenario-{run_tag}-s06"},
                   "artifacts": []})

    r.run(
        "S07-401-no-key", "POST without x-api-key",
        """A well-formed gate body with no x-api-key header at all. Verifies the
webhookAuth preHandler rejects with 401 and the documented body
{error: 'Webhook authentication failed', message: 'No x-api-key provided'}
(webhook/auth.ts:84-88). Also confirms WEBHOOK_DEV_BYPASS is NOT active on
this environment (a bypass would return 200).""",
        401, "POST", GATE_PATH,
        json_body=gate_body(cid_base + 1, f"scenario-{run_tag}-s07"))

    r.run(
        "S08-401-bad-key", "POST with bogus x-api-key",
        """Same body as S07 but with a made-up key. The key is sha256-hashed and
looked up against api_keys joined to ACTIVE customers; no match returns the
401 'Invalid or revoked API key' variant (webhook/auth.ts:42-48). Confirms
invalid and absent keys are distinguishable in the response message but
identical in status code.""",
        401, "POST", GATE_PATH,
        headers={"x-api-key": "scenario-bogus-key-" + run_tag},
        json_body=gate_body(cid_base + 2, f"scenario-{run_tag}-s08"))

    r.run(
        "S13-413-oversize", "Body over 1MB bodyLimit",
        """A ~1.1MB JSON body, exceeding the route's bodyLimit of 1,048,576 bytes
(ingest-soar.ts:166). Fastify aborts during parsing with
FST_ERR_CTP_BODY_TOO_LARGE -> 413 via the global error handler. Guards
against oversized SOAR containers (e.g. runaway artifact counts) consuming
gate memory.""",
        413, "POST", GATE_PATH,
        headers={"Content-Type": "application/json"},
        raw_body='{"container":{"id":1,"source_data_identifier":"big","pad":"'
                 + ("x" * 1_100_000) + '"},"artifacts":[]}')

    r.run(
        "S14-content-type", "Non-JSON content type",
        """POST with Content-Type: text/plain. The code map predicted 415
(FST_ERR_CTP_INVALID_MEDIA_TYPE), but Apollo registers a permissive content
parser, so the body parses as a string and the zod schema rejects it with
400 'body/ Expected object, received string' instead. This scenario
documents that the 415 branch is effectively unreachable for text/plain;
the practical contract is 400.""",
        "415 or 400", "POST", GATE_PATH,
        headers={"Content-Type": "text/plain"},
        raw_body="container=1")

    r.run(
        "S15-resolve-unauth", "GET /resolve with valid query, no key",
        """The preflight endpoint GET /api/v1/ingest/soar/resolve?client=<name>
with a valid query but no x-api-key. Verifies auth ordering on this route:
the querystring validates (so no 400), then the webhookAuth preHandler
rejects 401 BEFORE the handler can produce its 200/404. Ensures the
resolver cannot be used unauthenticated as a customer-name oracle.""",
        401, "GET", RESOLVE_PATH, params={"client": "zzz-scenario-unknown"})

    r.run(
        "S16-resolve-no-query", "GET /resolve missing query param",
        """GET /resolve with no client query parameter. Querystring schema
validation fails -> 400, again before auth (no key sent). Complements S15:
together they prove the validation-then-auth ordering on the resolve
route from both sides.""",
        400, "GET", RESOLVE_PATH)

    # ---- Authenticated scenarios ----
    auth_skip = None if args.api_key else "no --api-key provided"

    r.run(
        "S01-200-new", "Fresh container -> NEW",
        """A never-seen synthetic container with a unique
source_data_identifier. The full happy path: auth -> tenant resolution ->
CEF-to-RawAlert -> normalization pipeline -> dedup engine -> 200 with
transition_state='new', verdict='NEW', a populated apollo_alert_id
(<slug>-<containerId>) and the unabridged dedup_engine object (contract
v1.2). Baseline for S02/S03.""",
        200, "POST", GATE_PATH, headers={**key_hdr, **cn_hdr},
        json_body=gate_body(cid_base + 10, f"scenario-{run_tag}-s01"),
        skip_reason=auth_skip)

    r.run(
        "S02-200-dup-idempotency", "Same container re-posted",
        """Byte-identical re-post of S01 (same container id, same
source_data_identifier). Exercises the container-idempotency short-circuit
(pipeline.ts:189-241): the (customer_id, source_alert_id) pair already
exists, so the pipeline returns DUPLICATE/match_type='source_alert_id'
WITHOUT calling the dedup engine (dedup_engine should be null,
apollo_alert_id null, confidence 1.0). Still HTTP 200: duplicates are a
body-level disposition, never a 409. This is the exact mechanism that
collapsed the lsu-18872 repeat deliveries.""",
        200, "POST", GATE_PATH, headers={**key_hdr, **cn_hdr},
        json_body=gate_body(cid_base + 10, f"scenario-{run_tag}-s01"),
        skip_reason=auth_skip)

    r.run(
        "S03-200-content-dedup", "New container id, identical content",
        """A NEW container id and source_data_identifier, but artifact content
identical to S01. The idempotency short-circuit does NOT fire (different
sdi), so this reaches the ml-service dedup engine, which should verdict
DUPLICATE (or SIMILAR) on content similarity. Distinguishes engine-driven
content dedup from container idempotency: here dedup_engine is populated
with layer/score detail and match_type comes from the engine.""",
        200, "POST", GATE_PATH, headers={**key_hdr, **cn_hdr},
        json_body=gate_body(cid_base + 11, f"scenario-{run_tag}-s03"),
        skip_reason=auth_skip)

    r.run(
        "S09-422-mismatch", "X-Client-Name mismatch vs event",
        """Gateway-scoped key posting a container whose EVENT names one real
customer (container.tenant_name='lsu') while the X-Client-Name header
asserts a DIFFERENT real customer ('tulane'). Both resolve to genuine
tenants, which is the dangerous case: resolving either would silently file
the alert under a plausible wrong tenant. The mismatch check runs BEFORE
resolution (gate-tenant.ts:73-84) and hard-fails 422 with
code='client_name_mismatch'. Guards against a SOAR playbook (stale
per-action header) writing one tenant's alerts into another's. No alert is
stored.""",
        422, "POST", GATE_PATH,
        headers={**key_hdr, "X-Client-Name": "tulane"},
        json_body=gate_body(cid_base + 12, f"scenario-{run_tag}-s09", tenant_name="lsu"),
        skip_reason=auth_skip)

    r.run(
        "S19-403-suspended", "Suspended customer -> hard 403",
        """Gateway-scoped key posting a container whose event resolves to a
customer that exists but is SUSPENDED (container.tenant_name='unknown', the
dev "Unknown (Pre-Validation)" tenant). Tenant resolution succeeds, then the
customer-status gate (acceptsIngestion() at customer-status.ts:60) rejects
with a hard 403 (ingest-soar.ts:243-261). This is deliberately NOT fail-open:
unlike a pipeline/engine failure (which returns 200 NEW), a suspended tenant
is a caller/config condition and the alert must be refused, not silently
filed. No alert row is created.""",
        403, "POST", GATE_PATH,
        headers={**key_hdr},
        json_body=gate_body(cid_base + 13, f"scenario-{run_tag}-s19", tenant_name="unknown"),
        skip_reason=auth_skip)

    r.run(
        "S11-404-resolve-unknown", "GET /resolve unknown client",
        """Authenticated preflight for a client name that matches no customer.
Expects 404 with code='client_not_found' (ingest-soar.ts:374-382) and no
alert side effects. This is how a SOAR playbook can validate its ClientName
mapping before going live.""",
        404, "GET", RESOLVE_PATH, headers=key_hdr,
        params={"client": "zzz-scenario-unknown"},
        skip_reason=auth_skip)

    r.run(
        "S12-200-resolve-known", "GET /resolve known client",
        """Authenticated preflight for a known-good client name (--client-name).
Expects 200 with the resolved customer identity. Positive control for S11
and confirmation that the credential's scope can see the tenant.""",
        200, "GET", RESOLVE_PATH, headers=key_hdr,
        params={"client": args.client_name or ""},
        skip_reason=auth_skip if args.client_name else (auth_skip or "no --client-name"))

    # ---- Documented-but-not-run scenarios ----
    for sid, title, desc in [
        ("S17-429", "Rate limit (write group)",
         """The gate sits in the 'write' rate-limit group (200/min, confirmed by
x-ratelimit-limit: 200 headers on live responses), not the 2000/min
'ingestion' group; classifyRoute only matches /alerts/webhook for
ingestion. Triggering it would require flooding dev with 200+ posts in a
minute, so it is documented but not executed."""),
        ("S18-503", "Backpressure",
         """MAX_CONCURRENT_GATES=50; the 51st concurrent in-flight gate gets a
retryable 503 (ingest-soar.ts:272-290). Requires a 50-connection concurrent
flood against dev; documented but not executed."""),
        ("S20-500", "Tenant-resolution DB fault",
         """A transient DB error during tenant resolution escapes the fail-open
envelope and 500s (the one asymmetry: the same fault later in the pipeline
returns 200 fail-open). Not injectable from outside; documented only."""),
        ("S21-timeout", "Gate timeout fail-open",
         """If the pipeline exceeds SOAR_GATE_TIMEOUT_MS (10s) the gate returns
200 with transition_state='new' and a 'timed out' reasoning rather than an
error status. Not reliably injectable from outside; documented only."""),
    ]:
        r.run(sid, title, desc, "n/a", "POST", GATE_PATH,
              skip_reason="by design; see description")

    r.summary()
    print(f"Log files: {log_dir}")


if __name__ == "__main__":
    main()
