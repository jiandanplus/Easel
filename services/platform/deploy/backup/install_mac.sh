#!/bin/bash
# Install the restore drill and the hourly R2 watch on the Mac that holds the backup private key.
#   install_mac.sh FEISHU_OPEN_ID              copy files, venv, drill.env (no secrets); safe to re-run
#   install_mac.sh --keys ACCOUNT_ID           prompt (no echo) for the read-only R2 key -> rclone.conf 0600
#   install_mac.sh --activate                  check R2 is readable, then load both launchd agents
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../../../.." && pwd)"
DEST="$HOME/Easel-build/backup-drill"

case "${1:-}" in
  --keys)
    python3 "$HERE/write_rclone_config.py" --account-id "${2:?account id}" --output "$DEST/rclone.conf"
    ;;
  --activate)
    RCLONE_CONFIG="$DEST/rclone.conf" rclone lsf "$(sed -n 's/^R2_REMOTE=//p' "$DEST/drill.env")" --files-only >/dev/null
    mkdir -p "$HOME/Library/Logs/Easel" "$HOME/Library/LaunchAgents"
    for name in com.easel.backup-drill com.easel.backup-watch; do
      sed "s#__HOME__#$HOME#g" "$HERE/$name.plist" > "$HOME/Library/LaunchAgents/$name.plist"
      launchctl bootout "gui/$(id -u)/$name" 2>/dev/null || true
      launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$name.plist"
    done
    echo "launchd agents loaded; logs in ~/Library/Logs/Easel/backup-{watch,drill}.log"
    ;;
  ou_*)
    test -f "$HOME/Easel-build/backup-keys/backup-private.pem" || { echo "missing backup private key"; exit 1; }
    install -d -m 700 "$DEST"
    install -m 644 "$HERE"/{restore_drill.py,offsite_sync.py,feishu.py,write_rclone_config.py} \
      "$REPO/services/platform/src/platform_service/media_backup_b2/legacy_sources/sealed_transfer.py" "$DEST/"
    test -x "$DEST/venv/bin/python" || uv venv -q "$DEST/venv" --python 3.12
    uv pip install -q --python "$DEST/venv/bin/python" 'cryptography>=42,<47'
    if [ ! -f "$DEST/drill.env" ]; then
      (umask 077; cat > "$DEST/drill.env" <<EOF
R2_REMOTE=r2:easel-backups/central
NEWAPI_R2_REMOTE=r2:easel-backups/newapi
HEALTH_SSH=easel-zhiyin
RCLONE_CONFIG=$DEST/rclone.conf
PRIVATE_KEY=$HOME/Easel-build/backup-keys/backup-private.pem
FEISHU_USER=$1
LARK_CLI=$(command -v lark-cli)
EOF
)
    fi
    grep -q '^NEWAPI_R2_REMOTE=' "$DEST/drill.env" || echo 'NEWAPI_R2_REMOTE=r2:easel-backups/newapi' >> "$DEST/drill.env"
    grep -q '^HEALTH_SSH=' "$DEST/drill.env" || echo 'HEALTH_SSH=easel-zhiyin' >> "$DEST/drill.env"
    echo "installed in $DEST"
    ;;
  *)
    sed -n '2,6p' "$0"; exit 2
    ;;
esac
