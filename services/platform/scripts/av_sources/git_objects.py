"""Verify a SHA-1 Git commit's raw identity chain, entirely offline.

This establishes object integrity, not provenance or signature authenticity.
The caller must provide a size-bounded loader: returning bytes inherently means
the loader has already allocated that object. No object is materialized here.
"""
from dataclasses import dataclass, fields
import hashlib
import re
import unicodedata


class GitObjectError(ValueError):
    """Malformed, unavailable, inconsistent, or over-budget Git input."""


@dataclass(frozen=True)
class Limits:
    max_members: int = 30_000
    max_depth: int = 64
    max_blob_bytes: int = 16 * 1024 * 1024
    max_total_bytes: int = 512 * 1024 * 1024
    max_metadata_bytes: int = 4 * 1024 * 1024
    max_object_bytes: int = 64 * 1024 * 1024
    max_loaded_bytes: int = 128 * 1024 * 1024
    max_path_bytes: int = 4096


_OID = re.compile(r'[0-9a-f]{40}\Z')
_MODES = {'40000': 'tree', '100644': 'blob', '100755': 'blob',
          '120000': 'symlink', '160000': 'gitlink'}
_IDENTITY = re.compile(rb'[^\n\x00<>]+ <[^\n\x00<>]*> (?:0|[1-9][0-9]*) [+-][0-9]{4}\Z')


def _oid(value):
    if not isinstance(value, str) or not _OID.fullmatch(value):
        raise GitObjectError('expected canonical lowercase SHA-1 object ID')
    if value == '0' * 40:
        raise GitObjectError('null SHA-1 object ID')
    return value


def _commit_tree(raw):
    header, separator, _message = raw.partition(b'\n\n')
    if not separator or b'\0' in header:
        raise GitObjectError('incomplete commit headers')
    seen = {}
    parents = set()
    previous = None
    for line in header.split(b'\n'):
        if line.startswith(b' '):
            if previous is None or previous in (b'tree', b'parent', b'author', b'committer'):
                raise GitObjectError('invalid commit continuation')
            continue
        key, space, value = line.partition(b' ')
        if not space or not value or not re.fullmatch(rb'[A-Za-z][A-Za-z0-9-]*', key):
            raise GitObjectError('invalid commit header')
        if previous is None and key != b'tree':
            raise GitObjectError('tree must be first commit header')
        if key == b'parent':
            if previous not in (b'tree', b'parent') or value in parents:
                raise GitObjectError('noncanonical parent headers')
            parents.add(value)
        elif key == b'author':
            if previous not in (b'tree', b'parent'):
                raise GitObjectError('noncanonical author header order')
        elif key == b'committer':
            if previous != b'author':
                raise GitObjectError('noncanonical committer header order')
        elif key != b'tree' and b'committer' not in seen:
            raise GitObjectError('extension header before required headers')
        if key in (b'tree', b'author', b'committer') and key in seen:
            raise GitObjectError('duplicate required commit header')
        if key in (b'tree', b'parent'):
            try:
                _oid(value.decode('ascii'))
            except (UnicodeError, GitObjectError) as exc:
                raise GitObjectError('invalid commit object reference') from exc
        if key in (b'author', b'committer'):
            if not _IDENTITY.fullmatch(value):
                raise GitObjectError('invalid commit identity or timestamp')
            zone = value[-4:]
            if int(zone[:2]) > 23 or int(zone[2:]) > 59:
                raise GitObjectError('invalid commit timezone')
        seen[key] = value
        previous = key
    if not all(key in seen for key in (b'tree', b'author', b'committer')):
        raise GitObjectError('missing required commit header')
    return seen[b'tree'].decode('ascii')


def _name(raw):
    try:
        name = raw.decode('utf-8', errors='strict')
    except UnicodeError as exc:
        raise GitObjectError('non UTF-8 path unsupported') from exc
    if (not name or name in ('.', '..') or any(c in name for c in '/\\:')
            or any(unicodedata.category(c).startswith('C') for c in name)
            or name.endswith((' ', '.'))):
        raise GitObjectError('unsafe tree entry name')
    folded = unicodedata.normalize('NFC', name).casefold()
    stem = folded.split('.')[0]
    if (folded in ('.git', 'git~1') or stem in {'con', 'prn', 'aux', 'nul'}
            or re.fullmatch(r'(com|lpt)[1-9]', stem)):
        raise GitObjectError('reserved tree entry name')
    return name, folded


def verify_commit_tree(commit_oid, read_object, limits=Limits()):
    """Return JSON-compatible manifest; raise GitObjectError on any failure.

    read_object(40-character lowercase SHA-1) -> (type: str, raw: bytes).
    SHA256 fields hash raw object contents (without the Git object header).
    Entries include directories, blobs and symlinks; gitlinks are separate and
    never loaded. Paths are strict UTF-8 and portable, preserving spelling.
    Budgets count path occurrences, including repeated blobs/trees.
    """
    _oid(commit_oid)
    if not isinstance(limits, Limits):
        raise GitObjectError('limits must be a Limits instance')
    for field in fields(limits):
        value = getattr(limits, field.name)
        if type(value) is not int or value < 0:
            raise GitObjectError('limits must be nonnegative integers')
    # Recursive walk is deliberately capped independently of caller settings.
    if limits.max_depth > 128:
        raise GitObjectError('maximum supported depth is 128')
    loaded_bytes = 0
    content_bytes = 0
    members = 0
    entries = []
    gitlinks = []
    active = set()
    paths = set()

    def load(oid, expected):
        nonlocal loaded_bytes
        try:
            result = read_object(oid)
        except Exception as exc:
            raise GitObjectError('object unavailable: ' + oid) from exc
        if not isinstance(result, tuple) or len(result) != 2:
            raise GitObjectError('loader must return (type, bytes)')
        kind, raw = result
        if kind != expected or type(raw) is not bytes:
            raise GitObjectError('wrong object type or non-bytes content: ' + oid)
        size = len(raw)
        cap = limits.max_blob_bytes if kind == 'blob' else limits.max_metadata_bytes
        if size > min(cap, limits.max_object_bytes):
            raise GitObjectError('object byte budget exceeded')
        loaded_bytes += size
        if loaded_bytes > limits.max_loaded_bytes:
            raise GitObjectError('loaded object byte budget exceeded')
        digest = hashlib.sha1()
        digest.update(kind.encode('ascii') + b' ' + str(size).encode('ascii') + b'\0')
        digest.update(raw)
        if digest.hexdigest() != oid:
            raise GitObjectError('object hash mismatch: ' + oid)
        return raw

    commit_raw = load(commit_oid, 'commit')
    tree_oid = _commit_tree(commit_raw)

    def walk(oid, prefix, depth):
        nonlocal members, content_bytes
        if oid in active:
            raise GitObjectError('cyclic tree')
        active.add(oid)
        raw = load(oid, 'tree')
        offset = 0
        previous_sort_key = None
        while offset < len(raw):
            members += 1
            if members > limits.max_members:
                raise GitObjectError('member budget exceeded')
            if depth + 1 > limits.max_depth:
                raise GitObjectError('path depth budget exceeded')
            space = raw.find(b' ', offset)
            nul = raw.find(b'\0', offset)
            if space < offset or nul <= space or nul + 21 > len(raw):
                raise GitObjectError('truncated tree entry')
            try:
                mode = raw[offset:space].decode('ascii')
            except UnicodeError as exc:
                raise GitObjectError('invalid tree mode') from exc
            if mode not in _MODES:
                raise GitObjectError('noncanonical or unsupported tree mode')
            name_raw = raw[space + 1:nul]
            name, folded = _name(name_raw)
            sort_key = name_raw + (b'/' if mode == '40000' else b'\0')
            if previous_sort_key is not None and sort_key <= previous_sort_key:
                raise GitObjectError('noncanonical Git tree ordering')
            previous_sort_key = sort_key
            path = prefix + name
            if len(path.encode('utf-8')) > limits.max_path_bytes:
                raise GitObjectError('path byte budget exceeded')
            normalized = unicodedata.normalize('NFC', prefix).casefold() + folded
            if normalized in paths:
                raise GitObjectError('duplicate or normalization collision')
            paths.add(normalized)
            child = _oid(raw[nul + 1:nul + 21].hex())
            offset = nul + 21
            kind = _MODES[mode]
            if kind == 'gitlink':
                gitlinks.append({'path': path, 'mode': mode, 'oid': child})
                continue
            if kind == 'tree':
                # Child contents are validated and hashed once by the recursive walk.
                child_raw = walk(child, path + '/', depth + 1)
            else:
                child_raw = load(child, 'blob')
                content_bytes += len(child_raw)
                if content_bytes > limits.max_total_bytes:
                    raise GitObjectError('total file content budget exceeded')
            entries.append({'path': path, 'mode': mode, 'kind': kind, 'oid': child,
                            'size': len(child_raw), 'sha256': hashlib.sha256(child_raw).hexdigest()})
        active.remove(oid)
        return raw

    root_raw = walk(tree_oid, '', 0)
    return {'commit_oid': commit_oid, 'commit_sha256': hashlib.sha256(commit_raw).hexdigest(),
            'tree_oid': tree_oid, 'tree_sha256': hashlib.sha256(root_raw).hexdigest(),
            'entries': sorted(entries, key=lambda entry: entry['path'].encode('utf-8')),
            'gitlinks': sorted(gitlinks, key=lambda entry: entry['path'].encode('utf-8')),
            'counts': {'members': members, 'content_bytes': content_bytes,
                       'loaded_bytes': loaded_bytes}}
