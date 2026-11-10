"""Explicit synthetic test authority. Importing this module performs no file I/O."""
from dataclasses import dataclass, field
import hashlib
import json
import os
import re
import stat
from threading import Lock

_IMAGE = 'postgres@sha256:9e2cf6287f25c3151f4c9421ccce2b17f3e60061c111a29165e0d2cdd9ff0a97'
_EXTERNAL_ADMIN_SHA = '416797dcb35c2f7ff89f33f006a75bfce7b1663055504b25f38464b4fdb30c0c'
_LIMIT = 16384
_selected = None
_lock = Lock()


@dataclass(frozen=True, repr=False)
class TestAuthority:
    format: str
    nonce: str
    container_id: str
    image: str
    host: str
    port: int
    database: str
    user: str
    password: str = field(repr=False)
    _directory: str = field(repr=False)
    _directory_identity: tuple = field(repr=False)

    def __repr__(self):
        return '<OwnedB2TestAuthority>'


def _invalid():
    raise ValueError('invalid explicit owned B2 test authority')


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid()
        result[key] = value
    return result


def _private_json(path, expected_directory=None):
    """Walk every literal component with no-follow; validate the opened objects."""
    directory_fd = file_fd = None
    try:
        if not isinstance(path, str) or not path.startswith('/'):
            _invalid()
        parts = path.split('/')[1:]
        if not parts or any(part in ('', '.', '..') for part in parts):
            _invalid()
        directory_fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in parts[:-1]:
            child_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                               dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child_fd
        parent = os.fstat(directory_fd)
        identity = (parent.st_dev, parent.st_ino)
        if (parent.st_uid != os.getuid() or stat.S_IMODE(parent.st_mode) != 0o700
                or (expected_directory is not None and identity != expected_directory)):
            _invalid()
        # NONBLOCK also prevents a hostile FIFO from blocking before fstat rejects it.
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                          dir_fd=directory_fd)
        info = os.fstat(file_fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > _LIMIT):
            _invalid()
        chunks = []
        size = 0
        while True:
            block = os.read(file_fd, _LIMIT + 1 - size)
            if not block:
                break
            chunks.append(block)
            size += len(block)
            if size > _LIMIT:
                _invalid()
        after = os.fstat(file_fd)
        if ((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            _invalid()
        value = json.loads(b''.join(chunks).decode('utf-8'), object_pairs_hook=_pairs)
        if not isinstance(value, dict):
            _invalid()
        return value, identity
    except (OSError, ValueError, TypeError, UnicodeError):
        raise ValueError('invalid explicit owned B2 test authority') from None
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)


def _load_authority(path):
    value, identity = _private_json(path)
    keys = {'format', 'nonce', 'container_id', 'image', 'host', 'port', 'database', 'user', 'password'}
    if set(value) != keys:
        _invalid()
    for key in keys - {'port'}:
        if type(value[key]) is not str:
            _invalid()
    nonce = value['nonce']
    if (value['format'] != 'easel-b2-owned-test-pg-v1'
            or re.fullmatch('[a-f0-9]{32}', nonce) is None
            or re.fullmatch('[a-f0-9]{64}', value['container_id']) is None
            or value['image'] != _IMAGE or value['host'] != '127.0.0.1'
            or type(value['port']) is not int or not 1024 <= value['port'] <= 65535
            or value['database'] != 'easel_s1_test_b2_' + nonce
            or (value['user'] != 'postgres'
                and (re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,62}', value['user']) is None
                     or hashlib.sha256(value['user'].encode('utf-8')).hexdigest() != _EXTERNAL_ADMIN_SHA))
            or re.fullmatch('[A-Za-z0-9_-]{32,128}', value['password']) is None):
        _invalid()
    directory, filename = path.rsplit('/', 1)
    if filename != 'authority.private.json' or directory.rsplit('/', 1)[-1] != 'easel-b2-owned-pg-' + nonce:
        _invalid()
    return TestAuthority(**value, _directory=directory, _directory_identity=identity)


def selected_authority():
    """First valid explicit selection is immutable, including after env clearing."""
    global _selected
    with _lock:
        if _selected is None:
            path = os.environ.get('EASEL_B2_TEST_AUTHORITY_FILE')
            if not path:
                _invalid()
            _selected = _load_authority(path)
        return _selected


def read_identity_json(authority, filename):
    if filename not in ('identity-dsn.private.json', 'identity-roles.safe.json'):
        _invalid()
    value, _ = _private_json(authority._directory + '/' + filename,
                             authority._directory_identity)
    return value
