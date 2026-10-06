# Apollo Dedup — Data Dictionary

> What data enters the dedup system, where it comes from, and how each field is used.
>
> Source of truth: `apollo/src/ingestion/` (adapters, dedup, pipeline)

---

## Pipeline Flow

```
Raw Vendor JSON
    │
    ├─► adapter.extractObservables(payload)  → ExtractedObservables
    │       │
    │       └─► Hash Dedup (Layer 1)
    │
    ├─► adapter.normalize(payload)           → STIX 2.1 Bundle
    │       │
    │       └─► Intelligent Dedup (Layer 2)
    │
    ▼
 Dedup Decision
```

Both layers run synchronously during ingestion. Layer 1 runs first; if it catches a
duplicate, Layer 2 is skipped entirely.

---

## Layer 1: Hash Dedup Input

### Source

`adapter.extractObservables(raw.payload)` — pulls flat fields directly from the raw
vendor JSON **before** STIX conversion.

### Schema: `ExtractedObservables`

```typescript
// apollo/src/ingestion/adapters/types.ts:74
interface ExtractedObservables {
  sourceIps:     string[];   // Attacker / endpoint IPs
  destIps:       string[];   // Target / remote IPs
  domains:       string[];   // Domain names found in payload
  hostnames:     string[];   // Machine / host names
  alertType:     string;     // Detection rule or alert name
  vendorAlertId?: string;    // Vendor's own ID for the alert
}
```

### What the Pipeline Passes to Hash Dedup

```typescript
// apollo/src/ingestion/pipeline.ts:417-422
dedup.check(normalizedResult, {
  sourceIps:     observables.sourceIps,
  destIps:       [...observables.destIps, ...observables.domains],  // domains merged into destIps
  alertCategory: observables.alertType,
  customerId:    effectiveCustomerId,
});
```

Note: `domains` are concatenated into `destIps` before hashing. The hash key is built from:

```
SHA-256( oneSourceIp | sorted(destIps + domains) | alertCategory | timeBucket )
```

One hash is generated **per source IP** (subset matching). Time bucket = `floor(timestamp / 30min)`.

### Per-Adapter Observable Extraction

#### CrowdStrike (`crowdstrike.adapter.ts`)

| Field | Source in Raw JSON | Notes |
|-------|-------------------|-------|
| `sourceIps` | `LocalIP` | Single endpoint IP |
| `destIps` | `NetworkAccesses[].RemoteAddress` | Capped at 10 entries; private/loopback IPv4 filtered out; IPv6 included (except fe80::) |
| `domains` | (empty) | CrowdStrike doesn't provide domain fields at top level |
| `hostnames` | `Hostname` or `ComputerName` | Raptor names take priority over legacy |
| `alertType` | `Name` or `DetectName` or `Tactic` | Falls back to `"CrowdStrike Detection"` |
| `vendorAlertId` | `CompositeId` or `DetectId` | |

#### Splunk (`splunk.adapter.ts`)

| Field | Source in Raw JSON | Notes |
|-------|-------------------|-------|
| `sourceIps` | `src_ip` or `src` from `_raw` or top-level fields; also `IOC` field if it's an IP | Deduplicated |
| `destIps` | `dest_ip` or `dest` from `_raw` or top-level fields | Deduplicated |
| `domains` | Non-IP values from `src`/`dest`; `IOC`/`url` field hostname | Deduplicated |
| `hostnames` | (empty for notable corpus) | Webhook path uses `src_host` |
| `alertType` | `search_name` or `orig_rule_title` or `rule_name` | Falls back to `"Splunk ES Notable"` |
| `vendorAlertId` | `source_guid` or `event_id` or `source_event_id` or `rule_id` | |

#### M365 Defender (`m365-defender.adapter.ts`)

| Field | Source in Raw JSON | Notes |
|-------|-------------------|-------|
| `sourceIps` | `evidence[].ipAddress` | Only IPv4 |
| `destIps` | (empty) | |
| `domains` | `evidence[].domainName` and `evidence[].senderAddress` | Email addresses end up here |
| `hostnames` | (empty) | |
| `alertType` | `title` | e.g. "Multi-stage incident involving..." |
| `vendorAlertId` | `id` | |

#### Sentinel (`sentinel.adapter.ts`)

| Field | Source in Raw JSON | Notes |
|-------|-------------------|-------|
| `sourceIps` | `Entities` JSON string → entries with `Type: "ip"` → `Address` | |
| `destIps` | (empty) | |
| `domains` | (empty) | |
| `hostnames` | `Entities` → `Type: "host"` → `HostName`; also `CompromisedEntity` | |
| `alertType` | `AlertName` | Falls back to `"Sentinel Alert"` |
| `vendorAlertId` | `SystemAlertId` | |

#### Elastic (`elastic.adapter.ts`)

| Field | Source in Raw JSON | Notes |
|-------|-------------------|-------|
| `sourceIps` | `source.ip` (nested) | |
| `destIps` | `destination.ip` (nested) | |
| `domains` | (empty) | |
| `hostnames` | `host.name` (nested) | |
| `alertType` | `kibana.alert.rule.name` | Falls back to `"Elastic Security Alert"` |
| `vendorAlertId` | — | |

#### Entra ID (`entra-id.adapter.ts`)

| Field | Source in Raw JSON | Notes |
|-------|-------------------|-------|
| `sourceIps` | `ipAddress` | Only IPv4 |
| `destIps` | (empty) | |
| `domains` | Domain extracted from `userPrincipalName` (after `@`) | |
| `hostnames` | (empty) | |
| `alertType` | `riskEventType` (camelCase → Title Case) | Falls back to `"Entra ID Risk Detection"` |
| `vendorAlertId` | `id` | |

#### Overkill (`overkill.adapter.ts`)

| Field | Source in Raw JSON | Notes |
|-------|-------------------|-------|
| `sourceIps` | `payload.source_ip` | |
| `destIps` | `payload.destination_ip` | |
| `domains` | (empty) | |
| `hostnames` | `payload.hostname` | |
| `alertType` | `event_type` | Falls back to `"Overkill Detection"` |
| `vendorAlertId` | `payload.detection_id` | |

#### Generic Fallback (`generic.adapter.ts`)

Searches for common field names across unknown vendors:

| Field | Source Fields Tried | Notes |
|-------|-------------------|-------|
| `sourceIps` | `src_ip`, `source_ip`, `sourceAddress`, `attacker_ip`, `remote_ip`, `client_ip` | First match wins; IPv4 only |
| `destIps` | `dest_ip`, `destination_ip`, `destinationAddress`, `target_ip`, `local_ip` | First match wins; IPv4 only |
| `domains` | (empty) | |
| `hostnames` | `hostname`, `host`, `computer_name`, `device_name`, `machine_name`, `ComputerName` | First match wins |
| `alertType` | `alert_name`, `rule_name`, `title`, `name`, `event_type`, `detection_name`, `search_name` | First match wins |
| `vendorAlertId` | — | |

---

## Layer 2: Intelligent Dedup Input

### Source

The pipeline passes the already-normalized STIX 2.1 bundle plus alert metadata.

### Schema: `IntelligentDedupInput`

```typescript
// apollo/src/ingestion/dedup/intelligent-dedup.ts:62
interface IntelligentDedupInput {
  alertId:    string;    // Apollo-generated nanoid
  source:     string;    // Adapter vendor type: "crowdstrike" | "splunk" | "sentinel" | "m365_defender" | "elastic" | "entra_id" | "overkill" | "generic"
  severity:   string;    // "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
  customerId: string;    // Tenant ID from ownership validation
  stixBundle: object;    // Full STIX 2.1 bundle (see below)
  ingestedAt: string;    // ISO 8601 timestamp
  useCase?:   string;    // Detection rule name (from extractObservables.alertType, omitted if "unknown")
}
```

### How the Pipeline Builds This

```typescript
// apollo/src/ingestion/pipeline.ts:521-529
intelligentDedup.check({
  alertId:    apolloMetadata.alertId,
  source:     adapter.vendorType,
  severity,
  customerId: effectiveCustomerId,
  stixBundle,                                                           // ← full normalized bundle
  ingestedAt: apolloMetadata.ingestedAt,
  useCase:    observables.alertType !== 'unknown' ? observables.alertType : undefined,
});
```

### STIX Bundle Structure

The bundle is a STIX 2.1 JSON object built by each adapter's `normalize()` method.
Intelligent dedup walks `bundle.objects[]` and extracts IOCs from typed SCO objects.

```json
{
  "type": "bundle",
  "id": "bundle--<uuid>",
  "objects": [
    { "type": "identity", "name": "CrowdStrike Falcon", ... },
    { "type": "ipv4-addr", "value": "10.0.1.5" },
    { "type": "ipv6-addr", "value": "2001:db8::1" },
    { "type": "domain-name", "value": "workstation-1" },
    { "type": "file", "hashes": { "SHA-256": "e3b0c44...", "MD5": "d41d8c..." } },
    { "type": "user-account", "user_id": "admin", "account_login": "root" },
    { "type": "url", "value": "https://evil.test/payload" },
    { "type": "email-addr", "value": "attacker@evil.test" },
    { "type": "process", "pid": 1234, "command_line": "cmd.exe /c whoami" },
    { "type": "network-traffic", "src_ref": "...", "dst_ref": "...", "protocols": ["tcp"] },
    { "type": "indicator", "pattern": "[file:hashes.'SHA-256' = 'e3b0c44...']" },
    { "type": "attack-pattern", "name": "T1059", ... }
  ]
}
```

### What Intelligent Dedup Extracts from the Bundle

Only **7 IOC types** are recognized. Everything else is ignored.

```typescript
// apollo/src/ingestion/dedup/intelligent-dedup.ts:47
const STIX_TO_IOC_TYPE = {
  'ipv4-addr':         'ip',
  'ipv6-addr':         'ip',
  'domain-name':       'domain',
  'url':               'url',
  'email-addr':        'email',
  'user-account':      'user',
  'hostname':          'hostname',
  'x-apollo-hostname': 'hostname',
};
```

#### Extraction Rules Per SCO Type

| STIX Type | Value Extracted | IOC Type | Notes |
|-----------|----------------|----------|-------|
| `ipv4-addr` | `.value` | `ip` | Handles array values via coerceToScalar |
| `ipv6-addr` | `.value` | `ip` | |
| `domain-name` | `.value` | `domain` | |
| `url` | `.value` | `url` | |
| `email-addr` | `.value` | `email` | |
| `user-account` | `.user_id` AND `.account_login` | `user` | Produces **two** IOCs if both present |
| `hostname` | `.value` | `hostname` | |
| `file` | Each value in `.hashes{}` | `file_hash` | One IOC per hash algorithm (SHA-256, SHA-1, MD5) |

#### What Is Ignored

These STIX types are present in the bundle but **not used** by dedup:

| STIX Type | Why Ignored |
|-----------|-------------|
| `process` | Command lines / PIDs are too noisy for similarity matching |
| `network-traffic` | Structural (refs to IPs) — the IPs themselves are already extracted |
| `indicator` | STIX pattern string — dedup works on raw observables instead |
| `attack-pattern` | MITRE technique ID — not an IOC |
| `identity` | Source system identity — not an IOC |
| `relationship` | STIX graph edges — not an IOC |
| `observed-data` | Container object — children already extracted |

---

## IOC Canonicalization

Before comparison, every extracted IOC is normalized to a deterministic canonical form.

```typescript
// apollo/src/ingestion/dedup/ioc-canonicalizer.ts
```

| IOC Type | Canonicalization | Example |
|----------|-----------------|---------|
| `ip` (v4) | Strip CIDR, remove leading zeros per octet | `010.000.001.005` → `10.0.1.5` |
| `ip` (v6) | Expand to full 8-group lowercase hex; strip CIDR; expand `::` | `::ffff:192.168.1.1` → `0000:0000:0000:0000:0000:ffff:c0a8:0101` |
| `domain` | Lowercase, trim, strip trailing FQDN dot | `Evil.Example.Org.` → `evil.example.org` |
| `hostname` | Same rules as domain | `WORKSTATION-1` → `workstation-1` |
| `url` | URL constructor normalization; remove default ports (80/443) | `HTTPS://Evil.Test:443/path` → `https://evil.test/path` |
| `email` | Lowercase, trim | `ATTACKER@Evil.Test` → `attacker@evil.test` |
| `user` | Lowercase, trim | `Admin` → `admin` |
| `file_hash` | Lowercase, trim | `E3B0C44298FC...` → `e3b0c44298fc...` |

---

## IOC Block-List Filtering

After extraction and canonicalization, low-specificity IOCs are removed before scoring.

```typescript
// apollo/src/ingestion/dedup/ioc-blocklist.ts
```

### Blocked IPs

| Category | Values |
|----------|--------|
| RFC 1918 (private) | `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16` |
| Public DNS resolvers | `8.8.8.8`, `8.8.4.4`, `1.1.1.1`, `1.0.0.1`, `9.9.9.9`, `149.112.112.112`, `208.67.222.222`, `208.67.220.220` |
| Loopback / broadcast | `0.0.0.0`, `127.0.0.1`, `255.255.255.255` |
| Link-local | `169.254.x.x` |

### Blocked Domains (and all subdomains)

```
microsoft.com    windows.com       windowsupdate.com
google.com       googleapis.com    gstatic.com
github.com       amazon.com        amazonaws.com
cloudflare.com   akamaized.net     fastly.net
cloudfront.net   akamai.com        example.com
localhost
```

### Never Blocked

`file_hash`, `url`, `email`, `user` IOCs are never filtered by the block-list.

---

## Scoring Inputs

After filtering, the surviving IOCs are scored against candidates from the Redis index.

### Similarity Scorer Input

```typescript
// apollo/src/ingestion/dedup/similarity-scorer.ts
computeDedupScore(
  alertAIOCs:              CanonicalIOC[],    // Incoming alert's filtered IOCs
  alertBIOCs:              CanonicalIOC[],    // Candidate alert's stored IOCs
  temporalDistanceMinutes: number,            // abs(ingestedAt_A - ingestedAt_B) / 60000
  options: {
    threshold:          number,               // From PolicyForge or default (0.75 borderline)
    minIndicatorTypes:  number,               // Default: 2
    useCaseA?:          string,               // Incoming alert's useCase
    useCaseB?:          string,               // Candidate alert's useCase
  }
)
```

### IOC Weights (SCORE-03)

```
file_hash:  1.0    (strongest identity signal)
url:        0.9
domain:     0.8
email:      0.7
hostname:   0.6
user:       0.5
ip:         0.3    (LOW for dedup — CDNs, NAT, cloud egress cause false merges)
```

### Temporal Decay Brackets

```
 0–5 min:    1.0
 5–30 min:   0.8
 30–60 min:  0.6
 1–6 hours:  0.4
 6–24 hours: 0.2
 >24 hours:  0.1
```

### Combined Score Formula

```
combinedScore = (weightedJaccardIOC * 0.8) + (temporalScore * 0.2)

if useCaseA == useCaseB (case-insensitive):
    combinedScore = min(combinedScore + 0.15, 1.0)
```

### Decision Thresholds (Defaults)

| Combined Score | Confidence Tier | Action |
|---------------|-----------------|--------|
| >= 0.90 | HIGH | Auto-suppress, group under canonical alert |
| 0.75 – 0.89 | BORDERLINE | Route to suppression review queue for analyst |
| < 0.75 | BELOW | Pass through as distinct alert |

Both conditions must be true for "duplicate" verdict:
1. `combinedScore >= borderlineThreshold`
2. `sharedTypes.size >= minIndicatorTypes` (default: 2 distinct IOC types)

---

## Use-Case Dedup Input

When no IOC match is found, the system falls back to detection-rule matching.

### Input Fields

| Field | Source | Example |
|-------|--------|---------|
| `useCase` | `extractObservables.alertType` | `"TS - Defender - EDR-based Malware Detection - Rule"` |
| `source` | `adapter.vendorType` | `"splunk"` |
| `customerId` | Pipeline ownership check | `"cust-abc123"` |
| Dedup key fields | Extracted from STIX bundle + alert metadata | `"destinationUserName:rapinder\|destinationHostName:dc01"` |

### Rule Normalization

Detection rule names are normalized before comparison by stripping vendor prefixes and suffixes:

**Stripped prefixes:** `TS -`, `TekStream -`, `Endpoint -`, `LaTeX -`, `LSU -`, `NYSUCS -`, plus
generic `{CustomerName} - {Product} -` patterns.

**Stripped suffixes:** ` - Rule`, ` - Alert`, ` - Detection`, ` (Custom)`, ` (v1)`

**Example:**
```
"TS - Defender - EDR-based Malware Detection - Rule"
  → "EDR-based Malware Detection"
  → canonical ID: "edr_malware_detection"
```

### Dedup Key Extraction

Per-rule `dedupKeys` define which fields form the composite key for comparison.
Fields are extracted from the STIX bundle with vendor-aware aliases:

```typescript
// Field alias map for cross-vendor resilience
{
  sourceUserName:      ['sourceUserId', 'src_user', 'src_username', 'actor', 'initiator', ...],
  destinationUserName: ['dest_user', 'dest_username', 'target_user', 'user', 'userName', ...],
  destinationHostName: ['dest_host', 'hostname', 'host', 'ComputerName', 'device_name', ...],
  sourceAddress:       ['src_ip', 'source_ip', 'src', 'attacker_ip', 'remote_ip'],
  fileHashSha256:      ['file_hash', 'sha256', 'hash', 'fileHash'],
  incidentId:          ['incident_id', 'event_id', 'alert_id', 'notable_id'],
  ...
}
```

Fields are searched in this order:
1. Alert-level metadata (top-level fields)
2. STIX SCO object properties
3. STIX SCO extension properties
4. STIX-type-aware fallback (e.g., `destinationHostName` → `domain-name` SCO `.value`)

Array-valued fields (common in Splunk SOAR CEF) are coerced to scalar via first-element extraction.
Stringified JSON arrays like `"['10.0.0.1', '10.0.0.2']"` are parsed and first element used.

---

## Summary: What Each Dedup Layer Sees

| Data Point | Hash Dedup | Intelligent Dedup |
|------------|:----------:|:-----------------:|
| Source IPs | yes | yes (via STIX `ipv4-addr`) |
| Dest IPs | yes | yes (via STIX `ipv4-addr`) |
| Domains | yes (merged into destIps) | yes (via STIX `domain-name`) |
| Hostnames | no | yes (via STIX `hostname` / `x-apollo-hostname`) |
| File hashes | no | yes (via STIX `file` → `.hashes`) |
| URLs | no | yes (via STIX `url`) |
| Email addresses | no | yes (via STIX `email-addr`) |
| Usernames | no | yes (via STIX `user-account`) |
| Alert name / rule | yes (as `alertCategory`) | yes (as `useCase`) |
| Severity | no | yes (for severity guard) |
| Vendor source | no | yes (for cross-vendor detection) |
| Customer ID | yes (Redis key namespace) | yes (Redis key namespace) |
| Timestamp | yes (30-min bucket) | yes (temporal decay scoring) |
| Process / command line | no | no |
| MITRE techniques | no | no |
| Network traffic metadata | no | no |
