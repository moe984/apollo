# SOAR push-gate test — DEV (FTCC)

> **Ready to test (2026-07-23).** Dev is on `d8d48f6` (parity with prod). The
> existing tenant-scoped FTCC key in `.env` still authenticates. The recent
> auth work does **not** touch the ingest path: apollo#559 gave `soar_gateway`
> keys the `ingest_only` role, but the SOAR gate never reads a role (it reads
> only the key's customer binding / scope), so pushes are unaffected. Run
> `./run.sh` and expect `transition_state=new` on a fresh push.

Same end-to-end test as `../prod`, pointed at **dev** as the **FTCC** tenant:

```
SOAR/harness --POST /api/v1/ingest/soar (x-api-key)--> apollo-dev
   -> STIX transform -> store -> CIP dedup -> verdict back
```

Endpoint: `https://apollo-dev.cosmos.tekstream.com/api/v1/ingest/soar`

## Prerequisites (FTCC is already provisioned on dev)

The current `dev/.env` holds a **live, tenant-scoped FTCC key** that still works
— tenant keys keep authenticating even though new ones are no longer issued (see
below). If pushes return `200`, you need nothing here. This section only matters
if that key is revoked or lost.

> **What changed (2026-07-23).** Operator decisions altered provisioning:
> - **apollo#530:** creating a customer no longer mints a key. `POST
>   /api/v1/customers` returns the customer only.
> - **apollo#537 / #554:** creating, listing and revoking keys is now
>   **`soc_admin` only** (a `soc_analyst` gets `403`). Keys are owned by the
>   creating role. Only `soar_gateway` scope is **issuable** — you can no
>   longer mint a new `tenant`-scoped key. Existing tenant keys are unaffected.
> - **apollo#559:** a `soar_gateway` key now authenticates as the least-
>   privileged `ingest_only` role (was `customer_admin`). This bounds a leaked
>   gateway key to "push alerts" and 403s it on every other route. **The ingest
>   test is unaffected** — the SOAR gate does not read the role.

### If FTCC does not exist yet

Sign into the dev dashboard **as a SOC admin** (the badge in the sidebar must
read “SOC Admin”; a SOC analyst cannot do this). Then, in the browser console:

```js
// 1. Create the customer (no key is returned — apollo#530).
await fetch('/api/v1/customers', {
  method: 'POST', credentials: 'include',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ name: 'FTCC', slug: 'ftcc' }),
}).then(r => r.json())   // -> { id: 'cust_...', ... }
```

Copy `id` (`cust_...`) into `.env` as `APOLLO_CUSTOMER_ID`.

### Minting a key (soc_admin session; soar_gateway scope only)

`tenant` scope is no longer issuable, so a fresh key is a **`soar_gateway`** key.
Unlike a tenant key, a gateway key is **not** bound to FTCC — it resolves each
alert's tenant from the alert itself. So pushes must name the tenant.

```js
// soc_admin session only. scope MUST be soar_gateway (tenant is not issuable).
await fetch(`/api/v1/customers/${'<ftcc-id>'}/api-keys`, {
  method: 'POST', credentials: 'include',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ name: 'soar-dev-test', scope: 'soar_gateway' }),
}).then(r => r.json())
// -> { apiKey: { raw: 'apollo_ak_...', ownerRole: 'soc_admin', scope: 'soar_gateway', ... } }
```

Copy `apiKey.raw` into `.env` as `APOLLO_API_KEY`. The raw value is shown in this
**one response only**; only its SHA-256 hash is stored. A prod key `401`s here.

Because a gateway key is not tenant-bound, each artifact's CEF block must carry
a `ClientName` of `FTCC` (or `x-client-name: FTCC` on the request) so the alert
files under FTCC. The current harness (`build_event`) does **not** add
`ClientName`, so a gateway key needs that field added first. With the
**existing tenant-scoped** key in `.env`, none of this applies — it is bound to
FTCC, so pushes file there automatically. That is why the existing key is the
path of least resistance and this section is a fallback.

## Run

```bash
./run.sh                          # single push  -> expect transition_state=new
./run.sh --replay                 # +replay same SDI -> duplicate (Tier-1 idempotency)
./run.sh --replay --content-replay # full 3-pass e2e (Tier-1 + CIP content dedup)
```

`run.sh` reads `dev/.env`, runs the `../prod` harness with the dev URL/key/FTCC,
and is Apollo-only (`--no-crosscheck`) until you set `GATEWAY_URL` + `CIP_API_KEY`.

## Config (`dev/.env`)

| var | meaning |
|---|---|
| `APOLLO_URL` | `https://apollo-dev.cosmos.tekstream.com` |
| `APOLLO_CUSTOMER_ID` | FTCC id `cust_gD-...` (sent as `x-customer-id`) |
| `APOLLO_API_KEY` | the dev FTCC `x-api-key` (paste after minting) |
| `GATEWAY_URL` / `CIP_API_KEY` | optional, for the CIP cross-check |
