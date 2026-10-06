#!/usr/bin/env bash
#
# Apollo local development environment, one command.
#
#   ./up.sh              start everything, open the authenticated dashboard
#   ./up.sh --rebuild    force-rebuild the app image from the current checkout
#   ./up.sh --populate   also load the 20-customer synthetic fleet (idempotent)
#
# What it runs (all Docker, nothing on the host):
#   apollo-postgres     database (compose, port 5499)
#   apollo-redis        event bus (compose, port 6399)
#   apollo-jwks         nginx serving the local signing key's JWKS
#   apollo-app          the production image, http://localhost:3015
#   apollo-auth-proxy   nginx that injects your cip_token into every request,
#                       http://localhost:3016  <- the URL this script opens
#   apollo-adminer      DB browser, http://localhost:8090
#
# Authentication: a real RS256 cip_token (platform admin, 30-day expiry) is
# minted against a local keypair in ~/.apollo-local-dev, and the app verifies
# it through its normal CIP SSO path via CIP_AUTH_JWKS_URI. No Apollo code is
# modified or bypassed. The proxy on 3016 attaches the token for you; the raw
# app on 3015 still requires auth, which keeps the login flow testable.
set -euo pipefail

APOLLO_REPO="${APOLLO_REPO:-$HOME/Projects/apollo}"
STATE_DIR="$HOME/.apollo-local-dev"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NETWORK=apollo_default
APP_PORT=3015
PROXY_PORT=3016
ADMINER_PORT=8090
DB_URL="postgresql://apollo:apollo_dev_password@apollo-postgres:5432/apollo_dev"

REBUILD=false
POPULATE=false
for arg in "$@"; do
  case "$arg" in
    --rebuild) REBUILD=true ;;
    --populate) POPULATE=true ;;
    *) echo "unknown flag: $arg"; exit 1 ;;
  esac
done

[ -d "$APOLLO_REPO" ] || { echo "Apollo repo not found at $APOLLO_REPO (set APOLLO_REPO)"; exit 1; }
[ -f "$APOLLO_REPO/.env" ] || { echo "$APOLLO_REPO/.env missing; copy .env.example first"; exit 1; }
mkdir -p "$STATE_DIR"

echo "==> infra (postgres + redis)"
(cd "$APOLLO_REPO" && docker compose up -d --wait)

echo "==> app image"
if $REBUILD || ! docker image inspect apollo:local >/dev/null 2>&1; then
  (cd "$APOLLO_REPO" && docker build --build-arg VITE_APOLLO_ASSISTANT_ENABLED=true -t apollo:local .)
fi

run_in_app() { # run a one-off command inside the app image on the compose network
  docker run --rm --network "$NETWORK" \
    --env-file "$APOLLO_REPO/.env" \
    -e DATABASE_URL="$DB_URL" \
    -e REDIS_URL="redis://apollo-redis:6379" \
    "$@"
}

echo "==> migrations"
run_in_app apollo:local node --import tsx/esm src/db/migrate.ts

CUSTOMERS=$(docker exec apollo-postgres psql -U apollo -d apollo_dev -tAc "SELECT COUNT(*) FROM customers" 2>/dev/null || echo 0)
if [ "$CUSTOMERS" = "0" ]; then
  echo "==> first run: seeding demo data"
  # migrate.ts post-seeds api_keys that scripts/seed.ts also inserts; clear
  # the table so the standalone seed does not die on the unique constraint.
  docker exec apollo-postgres psql -U apollo -d apollo_dev -c "DELETE FROM api_keys" >/dev/null
  run_in_app apollo:local node --import tsx/esm scripts/seed.ts
fi

if $POPULATE; then
  echo "==> populating 20-customer synthetic fleet"
  run_in_app -v "$SCRIPT_DIR/populate.mjs":/app/populate.mjs apollo:local node populate.mjs
  docker exec -i apollo-postgres psql -U apollo -d apollo_dev -f - < "$SCRIPT_DIR/score-local-alerts.sql" >/dev/null
fi

echo "==> minting local cip_token (reuses keypair in $STATE_DIR)"
docker run --rm -v "$STATE_DIR":/out -v "$SCRIPT_DIR/mint.mjs":/app/mint.mjs \
  -e OUT_DIR=/out -e HOME=/tmp apollo:local node mint.mjs
TOKEN=$(cat "$STATE_DIR/cip_token.txt")

echo "==> jwks sidecar"
docker rm -f apollo-jwks >/dev/null 2>&1 || true
docker run -d --name apollo-jwks --network "$NETWORK" \
  -v "$STATE_DIR/jwks.json":/usr/share/nginx/html/jwks.json:ro nginx:alpine >/dev/null

echo "==> apollo app on :$APP_PORT"
docker rm -f apollo-app >/dev/null 2>&1 || true
docker run -d --name apollo-app --network "$NETWORK" -p "$APP_PORT":3000 \
  --env-file "$APOLLO_REPO/.env" \
  -e PORT=3000 \
  -e DATABASE_URL="$DB_URL" \
  -e REDIS_URL="redis://apollo-redis:6379" \
  -e CIP_AUTH_JWKS_URI="http://apollo-jwks/jwks.json" \
  apollo:local >/dev/null

echo "==> auth proxy on :$PROXY_PORT"
# The proxy attaches the minted token as the cip_token cookie on every
# request, keeping any cookies the browser sends after it. SSE needs
# buffering off; the Upgrade block keeps websockets working.
{
  printf 'server {\n  listen 80;\n  location / {\n'
  printf '    proxy_pass http://apollo-app:3000;\n'
  printf '    proxy_http_version 1.1;\n'
  printf '    proxy_set_header Host $host;\n'
  printf '    proxy_set_header Upgrade $http_upgrade;\n'
  printf '    proxy_set_header Connection $http_connection;\n'
  printf '    proxy_set_header Cookie "cip_token=%s; $http_cookie";\n' "$TOKEN"
  printf '    proxy_buffering off;\n    proxy_read_timeout 1h;\n'
  printf '  }\n}\n'
} > "$STATE_DIR/authproxy.conf"
docker rm -f apollo-auth-proxy >/dev/null 2>&1 || true
docker run -d --name apollo-auth-proxy --network "$NETWORK" -p "$PROXY_PORT":80 \
  -v "$STATE_DIR/authproxy.conf":/etc/nginx/conf.d/default.conf:ro nginx:alpine >/dev/null

echo "==> adminer on :$ADMINER_PORT"
if ! docker ps --format '{{.Names}}' | grep -q '^apollo-adminer$'; then
  docker rm -f apollo-adminer >/dev/null 2>&1 || true
  docker run -d --name apollo-adminer --network "$NETWORK" -p "$ADMINER_PORT":8080 \
    -e ADMINER_DEFAULT_SERVER=apollo-postgres adminer >/dev/null
fi

echo "==> waiting for health"
until curl -sf "http://localhost:$APP_PORT/health" >/dev/null 2>&1; do sleep 1; done
until curl -sf "http://localhost:$PROXY_PORT/health" >/dev/null 2>&1; do sleep 1; done

echo ""
echo "Apollo local dev is up:"
echo "  Dashboard (authenticated):  http://localhost:$PROXY_PORT/dashboard/"
echo "  Raw app (login required):   http://localhost:$APP_PORT/dashboard/"
echo "  Adminer (apollo / apollo_dev_password / apollo_dev): http://localhost:$ADMINER_PORT"
echo ""
open "http://localhost:$PROXY_PORT/dashboard/"
