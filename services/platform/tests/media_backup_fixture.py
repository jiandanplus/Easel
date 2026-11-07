"""Synthetic inputs only, shared by the import-only B1 file tests."""
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

FILE_NAMES = ('db/central.dump', 'db/identity.dump', 'db/central.roles', 'db/identity.roles',
              'config/central', 'config/identity', 'config/media')

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()

@contextmanager
def capture(root, blob_specs=()):
    root = Path(root)
    root.mkdir(mode=0o700)
    media = root / 'media'; media.mkdir(mode=0o700)
    fixed = root / 'fixed'; fixed.mkdir(mode=0o700)
    parent = root / 'restores'; parent.mkdir(mode=0o700)
    fds = {}; rows = []; blobs = []; references = []
    uid, gid = os.geteuid(), os.getegid()
    media_fd = parent_fd = None
    try:
        for index, name in enumerate(FILE_NAMES):
            raw = ('synthetic:' + name).encode()
            path = fixed / str(index); path.write_bytes(raw); path.chmod(0o600)
            fds[name] = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            rows.append(dict(name=name, size=len(raw), sha256=hashlib.sha256(raw).hexdigest(), uid=uid, gid=gid, mode=0o600))
        for index, spec in enumerate(blob_specs):
            raw, state, present = spec
            key = f'{index + 1:064x}'
            if present:
                path = media / key; path.write_bytes(raw); path.chmod(0o600)
            row = dict(key=key, state=state, present=present, size=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                       uid=uid, gid=gid, mode=0o600, tenant_id='tenant', product_id='easel', operation_id=f'op{index}',
                       missing_evidence_sha256='e' * 64 if state == 'staged' and not present else None)
            blobs.append(row)
            references.append({key: row[key] for key in ('key', 'tenant_id', 'product_id', 'operation_id')})
        plan = dict(format='easel-media-closure-v1', snapshots=dict(central='central-1', identity='identity-1',
                    source_sha256='a' * 64, schema_sha256='b' * 64), files=sorted(rows, key=lambda row: row['name']),
                    blobs=blobs, references=references,
                    registry_sha256=hashlib.sha256(canonical(dict(blobs=blobs, references=references))).hexdigest())
        media_fd = os.open(media, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        yield SimpleNamespace(plan=plan, media=media, fixed=fixed, parent=parent, fds=fds,
                              media_fd=media_fd, parent_fd=parent_fd, owner=(uid, gid))
    finally:
        for fd in (*fds.values(), media_fd, parent_fd):
            if fd is not None:
                os.close(fd)

