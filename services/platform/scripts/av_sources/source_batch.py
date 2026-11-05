"""Coordinate two fixed quarantine fetches under one cooperative budget.

No objects/signatures are authenticated here. No OS wall/memory/DNS isolation
is established. A single trusted writer owns the private parent; malicious
same-UID mutation is outside the original private-directory helper boundary.
Persisted candidate receipts are observations, not adoption or final freshness
proof: a late I/O/deadline failure can leave an orphan candidate receipt.
"""
from dataclasses import asdict, replace
import hashlib
import json
import math
import os
import stat
import struct
import time

from av_sources.bounded_fetch import (HttpLimits, _SOURCES, _new_directory,
                                     _new_file, _validate_limits, fetch_pack)
from av_sources.smart_http import SourceTransportError, parse_advertisement

_FLAGS = dict(source_identity_verified=False, build_inputs_adopted=False,
              os_resource_limits_verified=False, dns_ip_egress_verified=False)
_COUNTERS = dict(response_body_bytes='total_body_bytes',
                 total_input_bytes='total_input_bytes',
                 metadata_bytes='metadata_bytes', request_count='max_requests')
_RECEIPT_KEYS = frozenset(('source_id', 'repository_url', 'commit_oid', 'destination',
    'request_count', 'response_body_bytes', 'total_input_bytes', 'metadata_bytes',
    'http_raw_input_metered', 'advertisement_sha256', 'pack',
    'transport_injected_for_tests', 'source_identity_verified',
    'build_inputs_adopted', 'os_resource_limits_verified'))
_PACK_KEYS = frozenset(('response_body_bytes', 'pack_bytes', 'pack_sha256',
    'pack_checksum_sha1_verified', 'objects_declared', 'objects_verified',
    'progress_bytes', 'shallow', 'packets'))


def _reject():
    raise SourceTransportError('invalid source batch receipt')


def _integer(value, maximum, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        _reject()
    return value


class _Clock:
    def __init__(self, clock, seconds):
        self.clock = clock
        self.previous = None
        self.deadline = None
        self.deadline = self.now() + seconds
        if not math.isfinite(self.deadline):
            raise SourceTransportError('invalid batch clock')

    def now(self):
        value = self.clock()
        if (type(value) not in (float, int) or not math.isfinite(value)
                or (self.previous is not None and value < self.previous)):
            raise SourceTransportError('invalid or decreasing batch clock')
        self.previous = value
        if self.deadline is not None and value >= self.deadline:
            raise SourceTransportError('source batch deadline exceeded')
        return value

    def remaining(self):
        return self.deadline - self.now()


def _file(directory_fd, name, maximum, clock, *, keep=False):
    """Hash actual regular private bytes; bounded reads and no link following."""
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1
                or before.st_size > maximum):
            _reject()
        sha256 = hashlib.sha256(); sha1 = hashlib.sha1()
        total = 0; head = b''; tail = b''; chunks = []
        while True:
            clock.now()
            chunk = stream.read(min(65536, maximum - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                _reject()
            sha256.update(chunk)
            head = (head + chunk)[:12]
            combined = tail + chunk
            if len(combined) > 20:
                sha1.update(combined[:-20])
            tail = combined[-20:]
            if keep:
                chunks.append(chunk)
        after = os.fstat(stream.fileno())
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        snapshot = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if snapshot(before) != snapshot(after) or snapshot(after) != snapshot(named):
            _reject()
        clock.now()
        return total, sha256.hexdigest(), head, sha1.digest() == tail, b''.join(chunks)


def _validate(receipt, source, destination, limits, injected, clock, root_fd):
    if type(receipt) is not dict or set(receipt) != _RECEIPT_KEYS:
        _reject()
    url, oid = _SOURCES[source]
    for key, value in dict(source_id=source, repository_url=url, commit_oid=oid,
                           destination=destination).items():
        if type(receipt[key]) is not str or receipt[key] != value:
            _reject()
    for key in ('source_identity_verified', 'build_inputs_adopted', 'os_resource_limits_verified'):
        if receipt[key] is not False:
            _reject()
    if (receipt['http_raw_input_metered'] is not (not injected)
            or receipt['transport_injected_for_tests'] is not injected):
        _reject()
    for key, limit in _COUNTERS.items():
        _integer(receipt[key], getattr(limits, limit))
    if receipt['request_count'] != 2:
        _reject()
    pack = receipt['pack']
    if type(pack) is not dict or set(pack) != _PACK_KEYS:
        _reject()
    for key in ('response_body_bytes', 'pack_bytes', 'progress_bytes'):
        _integer(pack[key], limits.response_body_bytes)
    _integer(pack['objects_declared'], 60001, 1)
    _integer(pack['packets'], 2000000, 3)
    if (pack['objects_verified'] is not False or pack['pack_checksum_sha1_verified'] is not True
            or type(pack['shallow']) is not list or pack['shallow'] not in ([], [oid])):
        _reject()
    child_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
    try:
        info = os.fstat(child_fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            _reject()
        advert = _file(child_fd, 'advertisement.bin', min(limits.metadata_bytes, limits.response_body_bytes), clock, keep=True)
        actual = _file(child_fd, 'source.pack', limits.response_body_bytes, clock)
        size, digest, header, checksum, _ = actual
        if (size < 32 or header[:4] != b'PACK' or not checksum
                or struct.unpack('>I', header[4:8])[0] not in (2, 3)
                or struct.unpack('>I', header[8:12])[0] != pack['objects_declared']
                or size != pack['pack_bytes'] or digest != pack['pack_sha256']
                or advert[1] != receipt['advertisement_sha256']):
            _reject()
        parse_advertisement(advert[4], target=oid, max_bytes=limits.metadata_bytes)
        if (receipt['response_body_bytes'] != advert[0] + pack['response_body_bytes']
                or pack['pack_bytes'] > pack['response_body_bytes']
                or pack['progress_bytes'] > pack['response_body_bytes']
                or not advert[0] <= receipt['metadata_bytes'] <= receipt['total_input_bytes']
                or receipt['response_body_bytes'] > receipt['total_input_bytes']):
            _reject()
        clock.now()
        # Rebuild a bounded plain structure; never persist caller dictionaries.
        return dict(source_id=source, repository_url=url, commit_oid=oid,
                    **{key: receipt[key] for key in _COUNTERS},
                    advertisement_sha256=advert[1], pack=dict(pack), **_FLAGS)
    finally:
        os.close(child_fd)


def _write(fd, name, receipt):
    payload = (json.dumps(receipt, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
    if len(payload) > 16384:
        raise SourceTransportError('batch receipt size exceeded')
    with _new_file(fd, name) as sink:
        if sink.write(payload) != len(payload):
            raise SourceTransportError('short batch receipt write')
        sink.flush()
        os.fsync(sink.fileno())
    os.fsync(fd)


def fetch_sources(destination, limits=HttpLimits(), *, _fetch=None, _clock=None):
    """Fetch fixed libplacebo then Vulkan-Headers, stop on any failure.

    _fetch and _clock are explicit offline seams, labeled separately. Remaining
    limits clip the existing fetcher's relative cooperative timer; our single
    absolute deadline gates dispatch, validation, durable I/O and final return.
    This cannot interrupt a blocking syscall or remove fetch_pack's internal
    relative timer setup slack. A Linux hard resource/network worker is still
    required. Failure receipts contain only validated prefix counters; in-flight
    bytes/requests are unknown, never zero. Original exceptions always propagate.
    """
    _validate_limits(limits)
    clock = _Clock(time.monotonic if _clock is None else _clock, limits.batch_seconds)
    injected = _fetch is not None
    fetch = fetch_pack if _fetch is None else _fetch
    clock.now()
    fd, absolute = _new_directory(destination)
    sources = []
    totals = {key: 0 for key in _COUNTERS}
    active = None
    observed = False

    def summary(status):
        return dict(status=status, sources=list(sources), **totals,
                    limits=asdict(limits), transport_injected_for_tests=injected,
                    clock_injected_for_tests=_clock is not None,
                    http_raw_input_metered=not injected, **_FLAGS)

    try:
        for source in ('libplacebo', 'vulkan_headers'):
            active = None
            observed = False
            remaining = {limit: getattr(limits, limit) - totals[key] for key, limit in _COUNTERS.items()}
            if any(value <= 0 for value in remaining.values()) or remaining['max_requests'] < 2:
                raise SourceTransportError('source batch budget exhausted')
            remaining['response_body_bytes'] = min(limits.response_body_bytes,
                remaining['total_body_bytes'], remaining['total_input_bytes'])
            seconds = clock.remaining()
            handed = replace(limits, **remaining, batch_seconds=min(limits.batch_seconds, seconds),
                             request_seconds=min(limits.request_seconds, seconds))
            _validate_limits(handed)
            clock.now()  # Recheck immediately before entering the fetcher.
            active = source
            child = os.path.join(absolute, source)
            receipt = fetch(source, child, handed)
            clock.now()
            validated = _validate(receipt, source, child, handed, injected, clock, fd)
            sources.append(validated)
            for key in totals:
                totals[key] += validated[key]
            observed = True
            clock.now()
            _write(fd, source + '-receipt.json', validated)
            clock.now()
        result = summary('candidate')
        clock.now()
        _write(fd, 'batch-receipt.json', result)
        clock.now()  # Includes flush/fsync cost; a late receipt is not success.
        return result
    except BaseException as error:
        failed = summary('failed')
        failed.update(accounting_complete=active is None or observed,
                      unobserved_source=active if active and not observed else None,
                      in_flight_counters=None)
        try:
            _write(fd, 'failure-receipt.json', failed)
        except BaseException:
            if hasattr(error, 'add_note'):
                error.add_note('Failure receipt could not be written; partial candidate remains failed.')
        raise
    finally:
        os.close(fd)
