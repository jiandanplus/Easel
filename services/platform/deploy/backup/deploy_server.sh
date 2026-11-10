#!/bin/bash
# Deploy the sealed daily backup and the hourly R2 sync to the production server, from this Mac.
#   deploy_server.sh ACCOUNT_ID [ssh-host]     prompts (no echo) for the read-write R2 key
# The key is piped over ssh into /etc/easel-backup/rclone.conf (0600); it is never on a command line.
# The existing daily backup unit is found by its ExecStart and switched to sealed_backup.py with a
# systemd drop-in (the original unit file is left untouched; delete the drop-in to roll back).
set -euo pipefail
ACCOUNT_ID="${1:?Cloudflare account id}"
HOST="${2:-easel-zhiyin}"
HERE="$(cd "$(dirname "$0")" && pwd)"

read -rsp "R2 Access Key ID (read-write): " KEY_ID; echo
read -rsp "R2 Secret Access Key: " SECRET; echo

ssh "$HOST" 'set -e; install -d -m 700 /etc/easel-backup /usr/local/lib/easel-backup /var/lib/easel-backup'
tar -C "$HERE" -cf - feishu.py offsite_sync.py sealed_backup.py write_rclone_config.py backup-public.pem \
    easel-offsite-sync.service easel-offsite-sync.timer | ssh "$HOST" 'tar -C /usr/local/lib/easel-backup -xf -'
printf '%s\n%s\n' "$KEY_ID" "$SECRET" | ssh "$HOST" \
  "python3 /usr/local/lib/easel-backup/write_rclone_config.py --stdin --account-id $ACCOUNT_ID --output /etc/easel-backup/rclone.conf"
unset KEY_ID SECRET

ssh "$HOST" 'bash -s' <<'REMOTE'
set -euo pipefail
cd /usr/local/lib/easel-backup
command -v rclone >/dev/null || apt-get install -y rclone
install -m 644 backup-public.pem /etc/easel-backup/backup-public.pem
test -f /etc/easel-backup/backup.env || (umask 077; printf 'R2_REMOTE=r2:easel-backups/central\nRCLONE_CONFIG=/etc/easel-backup/rclone.conf\n' > /etc/easel-backup/backup.env)
rclone --config /etc/easel-backup/rclone.conf lsf r2:easel-backups/central --files-only >/dev/null

units=$(grep -l 'encrypted_backup' /etc/systemd/system/*.service 2>/dev/null | grep -v easel-offsite-sync || true)
[ "$(echo "$units" | grep -c .)" = 1 ] || { echo "expected exactly one unit running encrypted_backup, found: ${units:-none}"; exit 1; }
unit=$(basename "$units")
install -d "/etc/systemd/system/$unit.d"
printf '[Service]\nExecStart=\nExecStart=/usr/bin/python3 -B /usr/local/lib/easel-backup/sealed_backup.py\n' > "/etc/systemd/system/$unit.d/sealed.conf"
install -m 644 easel-offsite-sync.service easel-offsite-sync.timer /etc/systemd/system/
systemctl daemon-reload
systemctl start "$unit"                                   # one sealed backup now
journalctl -u "$unit" -n 3 --no-pager -o cat
systemctl start easel-offsite-sync.service                 # upload it
journalctl -u easel-offsite-sync.service -n 3 --no-pager -o cat
systemctl enable --now easel-offsite-sync.timer
echo "daily backup unit: $unit (drop-in $unit.d/sealed.conf)"
REMOTE
