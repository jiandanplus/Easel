#!/usr/bin/env bash
# Creates /srv/easel/newapi/.env once and starts the stack. Secrets are generated
# here and never printed. Safe to run again: an existing .env is kept.
set -euo pipefail
umask 077
dir=/srv/easel/newapi
install -d -m 0700 "$dir"
install -m 0600 "$(dirname "$0")/compose.yml" "$dir/compose.yml"
if [ ! -f "$dir/.env" ]; then
  {
    echo "PG_PASSWORD=$(openssl rand -hex 24)"
    echo "SESSION_SECRET=$(openssl rand -hex 32)"
    echo "CRYPTO_SECRET=$(openssl rand -hex 32)"
  } > "$dir/.env"
fi
docker compose --project-directory "$dir" -f "$dir/compose.yml" up -d --wait --wait-timeout 180
