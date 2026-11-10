#!/bin/bash
# Create the off-site backup bucket with the Cloudflare CLI (`cf`), once R2 is enabled on the account.
# Objects under central/ are locked for 90 days (not even the server's own token can delete them)
# and expire after 400 days. Idempotent: re-running only re-applies the rules.
set -euo pipefail
BUCKET="${BUCKET:-easel-backups}"
CF="${CF:-cf}"
: "${CLOUDFLARE_ACCOUNT_ID:?set CLOUDFLARE_ACCOUNT_ID (cf auth whoami shows it)}"
export CLOUDFLARE_ACCOUNT_ID

if ! "$CF" r2 buckets get "$BUCKET" >/dev/null 2>&1; then
  "$CF" r2 buckets create --name "$BUCKET" --location-hint apac
fi
"$CF" r2 buckets locks update "$BUCKET" --rules '[{"id":"central-90d","enabled":true,"prefix":"central/","condition":{"type":"Age","maxAgeSeconds":7776000}}]'
"$CF" r2 buckets lifecycle update "$BUCKET" --rules '[{"id":"central-expire-400d","enabled":true,"conditions":{"prefix":"central/"},"deleteObjectsTransition":{"condition":{"type":"Age","maxAge":34560000}}}]'
"$CF" r2 buckets locks get "$BUCKET"
echo "R2 endpoint: https://$CLOUDFLARE_ACCOUNT_ID.r2.cloudflarestorage.com  bucket: $BUCKET"
