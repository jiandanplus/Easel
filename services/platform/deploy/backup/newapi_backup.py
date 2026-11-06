#!/usr/bin/env python3
"""Daily sealed dump of the New API database (channels, supplier keys, tokens).

Sealed with the same off-host public key as the central backup and copied to its
own R2 prefix, so the central restore drill stays untouched. The old gateway had
no off-host copy, and losing that host lost every channel.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import uuid

import sealed_backup

DIRECTORY = Path("/var/backups/easel-newapi")
# Request logs can outgrow the 32 MiB sealing limit and are not needed to rebuild the gateway.
DUMP = ["docker", "exec", "easel-newapi-pg", "pg_dump", "-U", "newapi", "-d", "newapi", "--format=custom",
        "--exclude-table-data=logs"]


def dump(run=subprocess.run):
    payload = run(DUMP, capture_output=True, check=True).stdout
    if not payload.startswith(b"PGDMP"):
        raise ValueError("unexpected dump format")
    return payload


def save(directory, envelope, backup_id, now):
    directory.mkdir(mode=0o700, exist_ok=True)
    name = f"{now.strftime('%Y%m%dT%H%M%SZ')}-{backup_id}.sealed"
    temporary = directory / ("." + name)
    with open(temporary, "xb") as output:
        output.write(envelope)
        output.flush()
        os.fsync(output.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, directory / name)
    return directory / name


def main():
    os.umask(0o077)
    backup = sealed_backup.load_frozen(sealed_backup.FROZEN)
    backup.check(os.geteuid() == 0 and socket.gethostname() == backup.TARGET)
    public = backup.read_fixed(sealed_backup.PUBLIC_KEY, 32768, private=False)
    payload = dump()
    backup_id = str(uuid.uuid4())
    # The frozen sealing helper only knows "central" and "logto"; the R2 prefix keeps this apart.
    metadata = {"transfer_id": backup_id, "kind": "central", "target": backup.TARGET,
                "expected_sha256": hashlib.sha256(payload).hexdigest(), "expected_length": len(payload)}
    envelope = backup.crypto().seal(public, payload, metadata)
    path = save(DIRECTORY, envelope, backup_id, dt.datetime.now(dt.timezone.utc))
    print(json.dumps({"status": "sealed", "path": str(path), "payload_bytes": len(payload)}))


if __name__ == "__main__":
    main()
