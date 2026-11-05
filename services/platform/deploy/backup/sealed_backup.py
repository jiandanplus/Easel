#!/usr/bin/env python3
"""Daily sealed backup that needs only the public key on this host.

Wraps the frozen encrypted_backup.py (its collect/archive/verify/save stay
byte-identical and hash-pinned). The plaintext archive is verified before
sealing and the saved file is compared byte for byte; whether the envelope
opens is proven by the weekly off-host restore drill, which holds the only
private key.
"""
import argparse
import importlib.util
import json
import os
import socket
import uuid
from pathlib import Path

FROZEN = Path("/root/easel-migration-20261024/encrypted_backup.py")
PUBLIC_KEY = Path("/etc/easel-backup/backup-public.pem")


def load_frozen(path):
    spec = importlib.util.spec_from_file_location("easel_frozen_encrypted_backup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(backup, public, *, euid, hostname):
    backup.check(euid == 0 and hostname == backup.TARGET)
    entries, manifest = backup.collect()
    payload = backup.archive(entries, manifest)
    backup.verify_payload(payload, manifest)
    helper = backup.crypto()
    backup_id = str(uuid.uuid4())
    metadata = {"transfer_id": backup_id, "kind": "central", "target": backup.TARGET,
                "expected_sha256": backup.digest(payload), "expected_length": len(payload)}
    envelope = helper.seal(public, payload, metadata)
    path = backup.save(envelope, backup_id, lambda value: backup.check(value == envelope))
    return {"status": "sealed", "backup_id": backup_id, "path": path,
            "ciphertext_bytes": len(envelope), "ciphertext_sha256": backup.digest(envelope),
            "payload_bytes": len(payload), "payload_sha256": backup.digest(payload),
            "file_count": len(manifest["files"]), "self_verify": "bytes"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frozen", type=Path, default=FROZEN)
    parser.add_argument("--public-key", type=Path, default=PUBLIC_KEY)
    args = parser.parse_args()
    os.umask(0o077)
    backup = load_frozen(args.frozen)
    public = backup.read_fixed(args.public_key, 32768, private=False)
    result = run(backup, public, euid=os.geteuid(), hostname=socket.gethostname())
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
