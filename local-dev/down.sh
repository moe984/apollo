#!/usr/bin/env bash
# Tear down the Apollo local dev environment.
#   ./down.sh          stop app-side containers, keep the database volume
#   ./down.sh --wipe   also remove postgres/redis and their volumes (fresh start)
set -euo pipefail
APOLLO_REPO="${APOLLO_REPO:-$HOME/Projects/apollo}"

docker rm -f apollo-app apollo-auth-proxy apollo-jwks apollo-adminer 2>/dev/null || true
if [ "${1:-}" = "--wipe" ]; then
  (cd "$APOLLO_REPO" && docker compose down -v)
  echo "wiped: containers and database volume removed"
else
  (cd "$APOLLO_REPO" && docker compose stop)
  echo "stopped: database volume kept; ./up.sh brings it back"
fi
