# Apollo local development environment

One command brings up the full Apollo stack in Docker and opens an already
authenticated dashboard.

```bash
./up.sh                # start everything, open http://localhost:3016/dashboard/
./up.sh --rebuild      # force-rebuild the image from the current apollo checkout
./up.sh --populate     # also load the 20-customer synthetic fleet with verdicts
./down.sh              # stop (keeps the database)
./down.sh --wipe       # stop and delete the database volume
```

Requirements: Docker Desktop running, the apollo repo cloned at
`~/Projects/apollo` (override with `APOLLO_REPO=/path ./up.sh`) with a `.env`
file present.

## What you get

| URL | What |
|-----|------|
| http://localhost:3016/dashboard/ | Dashboard, authenticated automatically |
| http://localhost:3015/dashboard/ | Same app without auto-auth (login flow testable) |
| http://localhost:8090 | Adminer DB browser (server `apollo-postgres`, user `apollo`, password `apollo_dev_password`, db `apollo_dev`) |

## How authentication works

No Apollo code is modified or bypassed. `mint.mjs` generates a local RS256
keypair (kept in `~/.apollo-local-dev/`, outside any repo) and signs a
30-day platform-admin `cip_token`. The app container is pointed at a tiny
nginx sidecar serving that keypair's JWKS via the standard
`CIP_AUTH_JWKS_URI` env var, so Apollo's real CIP SSO verifier validates the
token exactly as production validates the real IdP's. The auth proxy on
port 3016 attaches the token as the `cip_token` cookie on every request.

The token is re-minted on every `./up.sh` run (same keypair, fresh 30-day
expiry), so expiry is never something you deal with.

## Files

- `up.sh` / `down.sh` - lifecycle
- `mint.mjs` - keypair + JWKS + token minting (runs inside the app image)
- `populate.mjs` - 20 synthetic customers, ~5k alerts over 30 days with
  business-hours bias and incident spikes
- `score-local-alerts.sql` - assigns dedup verdicts (NEW / DUPLICATE /
  SIMILAR, ~5% left unscored) to the populated alerts and parents
  duplicates onto canonicals

Everything is local: the database volume, the keypair, and the token never
leave your machine, and nothing here touches dev or prod.
