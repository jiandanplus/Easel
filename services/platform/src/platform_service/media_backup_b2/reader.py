"""Fresh-process trusted context before archive; no DB/source-facts restore claim."""
import hashlib
import io
import os
import struct
import threading

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from media_backup.contract import decode_manifest, physical_size, registry_digest

from .contracts import B2Error, ExpectedV2, require
from . import wire
from .anchor import RootAnchorIntent, _record
from .durable import _File
from .stream import V2Decoder


def _recipient(private_pem):
    require(type(private_pem) is bytes and 1 <= len(private_pem) <= 32768)
    key = serialization.load_pem_private_key(private_pem, password=None)
    require(isinstance(key, rsa.RSAPrivateKey) and 3072 <= key.key_size <= 8192
            and key.public_key().public_numbers().e == 65537)
    raw = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(raw).hexdigest()


def _pread(source, size, offset):
    source.verify()
    require(type(size) is int and 0 < size <= wire.MAX_HEADER and offset + size <= source.size)
    raw = bytearray()
    while len(raw) < size:
        block = os.pread(source._fd, size - len(raw), offset + len(raw))
        require(type(block) is bytes and 0 < len(block) <= size - len(raw))
        raw.extend(block)
    source.verify()
    return bytes(raw)


def _context_length(source):
    prefix = _pread(source, 12, 0)
    require(prefix[:8] == wire.MAGIC)
    size = struct.unpack(">I", prefix[8:])[0]
    require(1 <= size <= wire.MAX_HEADER)
    header = wire.decode_header(_pread(source, size, 12))
    require(header["kind"] == "central-context")
    return header["plaintext_length"]


class OwnedArchiveReader:
    """Owns duplicated descriptors/trust bindings, never the caller's root FD."""
    def __init__(self, intent, directory, files, decoder, reference, object_row, *, check=None):
        require(check is None or callable(check))
        self._guard = check if check is not None else lambda: None
        self._intent, self._directory, self._files = intent, directory, files
        self._decoder, self._reference, self._row = decoder, reference, dict(object_row)
        self._lock = threading.RLock()
        self._closed = self._failed = self._verified = False
        self._receipt = None
        self._check()

    def _check(self):
        self._guard()
        require(not self._closed and not self._failed)
        self._intent._check()
        self._directory.verify()
        for source in self._files:
            source.verify()
        self._guard()

    @property
    def verified(self):
        return self._verified and not self._failed

    @property
    def receipt(self):
        require(self.verified)
        return dict(self._receipt)

    def read(self, size):
        with self._lock:
            try:
                self._check()
                raw = self._decoder.read(size)
                self._check()
                if raw == b"":
                    require(self._decoder.verified)
                    result = self._decoder.receipt
                    require(result["ciphertext_bytes"] == self._row["bytes"]
                            and result["ciphertext_sha256"] == self._row["sha256"]
                            and result["plan_sha256"] == self._reference.plan_sha256
                            and result["registry_sha256"] == self._reference.registry_sha256)
                    self._verified, self._receipt = True, result
                self._check()
                return raw
            except BaseException as error:
                self._failed = True
                self.close()
                if not isinstance(error, Exception): raise
                raise B2Error() from None

    def fileno(self):
        raise io.UnsupportedOperation()

    def close(self):
        with self._lock:
            if self._closed: return
            self._closed = True
            self._decoder.close()
            for source in reversed(self._files):
                source.close()
            self._directory.close()
            self._intent.close()

    def __reduce_ex__(self, protocol): raise TypeError("owned reader")
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def read_committed(root_anchor_intent, backup_job_fd, private_pem, *, check=None):
    intent = directory = decoder = context_decoder = None
    files = []
    try:
        require(check is None or callable(check))
        guard = check if check is not None else lambda: None
        guard()
        require(type(root_anchor_intent) is RootAnchorIntent)
        intent = root_anchor_intent._clone()
        reference = intent._read_anchor()
        directory = intent._open_job(backup_job_fd)
        anchor_source = _File(intent._directory, intent.backup_id + ".json", wire.MAX_COMMIT)
        files.append(anchor_source)
        require(anchor_source.read_all(wire.MAX_COMMIT) == _record(reference))
        commit_source = _File(directory, "COMMITTED.json", wire.MAX_COMMIT)
        files.append(commit_source)
        raw_commit = commit_source.read_all(wire.MAX_COMMIT)
        require(intent._validate_commit(raw_commit) == reference)
        commit = wire.decode_commit(raw_commit)
        context_row = commit["objects"]["context"]
        context_source = _File(directory, "context.easelbs2", wire.cipher_budget("central-context"))
        files.append(context_source)
        context_source.hash_all(context_row["bytes"], context_row["sha256"], check=guard)
        guard()
        # Header length is usable only after its entire ciphertext was bound by
        # the independent trusted commit. It is never archive-manifest bootstrap.
        context_length = _context_length(context_source)
        guard()
        recipient = _recipient(private_pem)
        guard()
        expected = ExpectedV2(reference.backup_id, "central-context", reference.target,
            recipient, context_length, reference.plan_sha256, reference.registry_sha256)
        context_decoder = V2Decoder(private_pem, context_source, expected)
        guard()
        context = bytearray()
        while True:
            guard()
            block = context_decoder.read(wire.CHUNK_BYTES)
            guard()
            if block == b"": break
            context.extend(block)
            require(len(context) <= wire.MAX_CONTEXT)
        receipt = context_decoder.receipt
        require(context_decoder.verified and receipt["ciphertext_bytes"] == context_row["bytes"]
                and receipt["ciphertext_sha256"] == context_row["sha256"]
                and hashlib.sha256(context).hexdigest() == reference.plan_sha256)
        guard()
        plan = decode_manifest(bytes(context))
        guard()
        require(plan["registry_sha256"] == reference.registry_sha256
                and registry_digest(plan["blobs"], plan["references"]) == reference.registry_sha256)
        context_decoder.close()
        context_decoder = None
        archive_row = commit["objects"]["archive"]
        archive_source = _File(directory, "archive.easelbs2", wire.cipher_budget("central-archive"))
        files.append(archive_source)
        archive_source.hash_all(archive_row["bytes"], archive_row["sha256"], check=guard)
        expected = ExpectedV2(reference.backup_id, "central-archive", reference.target, recipient,
            physical_size(plan), reference.plan_sha256, reference.registry_sha256)
        decoder = V2Decoder(private_pem, archive_source, expected)
        guard()
        value = OwnedArchiveReader(intent, directory, files, decoder, reference, archive_row, check=guard)
        guard()
        return plan, value
    except BaseException as error:
        if context_decoder is not None: context_decoder.close()
        if decoder is not None: decoder.close()
        for source in reversed(files): source.close()
        if directory is not None: directory.close()
        if intent is not None: intent.close()
        if not isinstance(error, Exception): raise
        raise B2Error() from None
