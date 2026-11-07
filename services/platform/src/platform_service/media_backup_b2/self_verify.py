"""Authenticated, bounded archive-content verification; no restore or publication.

The caller retains the maintenance window and actual ciphertext descriptors.
This pass never makes a root anchor or substitutes for capture postconditions.
"""

import hashlib

from media_backup.contract import (
    Limits,
    encode_manifest,
    member_records,
    physical_size,
    validate_plan,
)
from media_backup.ustar import TarReader

from .contracts import B2Error, require
from .reader import OwnedArchiveReader
from .stream import V2Decoder

MAX_BUNDLES_BYTES = 32 * 1024**2
_CONFIG = frozenset(("config/central", "config/identity", "config/media"))


def _verify_bundles(bundles, records, plan):
    # A fixed implementation, never a caller-supplied validation bypass.
    from .source_facts import verify_archived_bundles

    return verify_archived_bundles(bundles, records, plan)


class _CheckedSource:
    def __init__(self, source, check, chunk_bytes):
        self.source, self.check, self.maximum = source, check, chunk_bytes
        self.digest = hashlib.sha256()

    def read(self, size):
        require(type(size) is int and 0 < size <= self.maximum)
        self.check()
        raw = self.source.read(size)
        require(type(raw) is bytes and len(raw) <= size)
        self.digest.update(raw)
        self.check()
        return raw


class _Discard:
    def write(self, raw):
        return len(raw)


class _Match:
    def __init__(self, expected):
        self.expected, self.offset = expected, 0

    def write(self, raw):
        size = len(raw)
        require(
            self.offset + size <= len(self.expected)
            and raw == self.expected[self.offset : self.offset + size]
        )
        self.offset += size
        return size


class _Collect:
    def __init__(self, size):
        self.size, self.raw = size, bytearray()

    def write(self, raw):
        require(len(self.raw) + len(raw) <= self.size)
        self.raw.extend(raw)
        return len(raw)


def verify_archive_content(decoder, expected_plan, *, check=None, limits=Limits()):
    """Consume one fresh authenticated stream and all raw-facts bundle checks.

    A failed pass closes the decoder and leaves any caller-owned private stage
    intact. Cancellation propagates. The optional trusted check is invoked at
    each bounded read and before returning; pipeline supplies its owner/deadline
    check. No decoder or facts receipt is accepted before authenticated EOF.
    """
    try:
        require(
            type(decoder) in (V2Decoder, OwnedArchiveReader)
            and (check is None or callable(check))
        )
        guard = check if check is not None else lambda: None
        guard()
        plan = validate_plan(expected_plan, limits)
        manifest = encode_manifest(plan, limits)
        plan_sha = hashlib.sha256(manifest).hexdigest()
        total = physical_size(plan, limits)
        require(type(MAX_BUNDLES_BYTES) is int and MAX_BUNDLES_BYTES > 0)
        require(
            sum(row["size"] for row in plan["files"] if row["name"] in _CONFIG)
            <= MAX_BUNDLES_BYTES
        )
        hashed = _CheckedSource(decoder, guard, limits.chunk_bytes)
        reader = TarReader(hashed, limits.max_archive_bytes, limits.chunk_bytes)
        require(reader.header() == ("manifest.json", len(manifest)))
        match = _Match(manifest)
        require(
            reader.body(len(manifest), match) == plan_sha
            and match.offset == len(manifest)
        )
        bundles = {}
        records = member_records(plan)
        for row in records:
            guard()
            require(reader.header() == (row["name"], row["size"]))
            sink = _Collect(row["size"]) if row["name"] in _CONFIG else _Discard()
            require(reader.body(row["size"], sink) == row["sha256"])
            if row["name"] in _CONFIG:
                require(len(sink.raw) == row["size"])
                bundles[row["name"]] = bytes(sink.raw)
            guard()
        count = reader.finish(total)
        guard()
        require(count == total and decoder.verified)
        final = decoder.receipt
        require(
            final["plaintext_length"] == total
            and final["plaintext_sha256"] == hashed.digest.hexdigest()
            and final["plan_sha256"] == plan_sha
            and final["registry_sha256"] == plan["registry_sha256"]
        )
        require(bundles.keys() == _CONFIG)
        facts = _verify_bundles(bundles, plan["files"], plan)
        require(
            type(facts) is dict
            and facts["source_facts_sha256"] == plan["snapshots"]["source_sha256"]
        )
        guard()
        return dict(
            status="archive_content_verified",
            file_count=len(records),
            blob_count=sum(row["present"] for row in plan["blobs"]),
            archive_bytes=count,
            archive_sha256=hashed.digest.hexdigest(),
            plan_sha256=plan_sha,
            registry_sha256=plan["registry_sha256"],
            source_facts_sha256=facts["source_facts_sha256"],
        )
    except BaseException as error:
        if type(decoder) in (V2Decoder, OwnedArchiveReader):
            try:
                decoder.close()
            except BaseException:
                pass
        if not isinstance(error, Exception):
            raise
        raise B2Error() from None
