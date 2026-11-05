"""Authenticated v2 streaming; explicit finish only, borrowed stream ownership.

Crypto binds actual bytes to independent expected digests, not capture/DB truth.
The context producer validates B1 before encoding; the fresh reader must parse
the authenticated B1 plan and registry before trusting archive ExpectedV2.
Python cannot promise erasure of immutable key bytes from process memory.
"""
import base64
from dataclasses import asdict
import hashlib
import io
import re
import struct

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
import nacl.bindings.crypto_secretstream as sodium

from .contracts import B2Error, ExpectedV2, require, validate_int
from . import wire


def _expected(value):
    require(type(value) is ExpectedV2)
    return ExpectedV2(**asdict(value))


def _pem(value, *, private=False):
    require(type(value) is bytes and 1 <= len(value) <= 32768)
    require(value.count(b"-----BEGIN ") == 1 and value.count(b"-----END ") == 1)
    require(re.fullmatch(rb"-----BEGIN ([A-Z ]+)-----\r?\n[A-Za-z0-9+/=\r\n]+-----END \1-----\r?\n?", value) is not None)
    key = (serialization.load_pem_private_key(value, password=None) if private else
           serialization.load_pem_public_key(value))
    require(isinstance(key, rsa.RSAPrivateKey if private else rsa.RSAPublicKey))
    public = key.public_key() if private else key
    require(3072 <= public.key_size <= 8192 and public.public_numbers().e == 65537)
    der = public.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return key, hashlib.sha256(der).hexdigest()


def _oaep():
    return padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=wire.MAGIC)


def _constants():
    require(sodium.crypto_secretstream_xchacha20poly1305_KEYBYTES == 32
            and sodium.crypto_secretstream_xchacha20poly1305_HEADERBYTES == 24
            and sodium.crypto_secretstream_xchacha20poly1305_ABYTES == wire.ABYTES
            and sodium.crypto_secretstream_xchacha20poly1305_TAG_MESSAGE == 0
            and sodium.crypto_secretstream_xchacha20poly1305_TAG_FINAL == 3)


def _raise(error):
    if not isinstance(error, Exception):
        raise error
    raise B2Error() from None


class V2Encoder:
    def __init__(self, public_pem, expected, cipher_sink):
        self._buffer = bytearray()
        self._failed = self._closed = self._finished = False
        try:
            _constants()
            self.expected, self._sink = _expected(expected), cipher_sink
            key, recipient = _pem(public_pem)
            require(recipient == self.expected.recipient_sha256)
            self._state = sodium.crypto_secretstream_xchacha20poly1305_state()
            secret = sodium.crypto_secretstream_xchacha20poly1305_keygen()
            header = sodium.crypto_secretstream_xchacha20poly1305_init_push(self._state, secret)
            wrapped = key.encrypt(secret, _oaep())
            self._hash, self._cipher_hash = hashlib.sha256(), hashlib.sha256()
            self._count = self._cipher_count = self._records = 0
            values = dict(version=2, algorithm=wire.ALGORITHM, backup_id=self.expected.backup_id,
                kind=self.expected.kind, target=self.expected.target, recipient_sha256=recipient,
                plaintext_length=self.expected.plaintext_length, chunk_bytes=wire.CHUNK_BYTES,
                wrapped_key=base64.b64encode(wrapped).decode("ascii"),
                stream_header=base64.b64encode(header).decode("ascii"))
            prefix = wire.header_prefix(values)
            self._prefix_sha = hashlib.sha256(prefix).digest()
            self._write_cipher(prefix)
        except BaseException as error:
            self._poison()
            _raise(error)

    @property
    def buffered_plaintext(self):
        return len(self._buffer)

    def _poison(self):
        self._failed = True
        self._buffer.clear()
        self._state = None

    def _live(self):
        require(not self._failed and not self._closed and not self._finished)

    def _write_cipher(self, raw):
        require(self._cipher_count + len(raw) <= wire.cipher_budget(self.expected.kind))
        view = memoryview(raw)
        while view:
            count = self._sink.write(view)
            require(type(count) is int and 0 < count <= len(view))
            self._cipher_hash.update(view[:count])
            self._cipher_count += count
            view = view[count:]

    def _frame(self, raw, tag):
        prefix = wire.record_prefix(self._records, len(raw) + wire.ABYTES)
        cipher = sodium.crypto_secretstream_xchacha20poly1305_push(
            self._state, raw, wire.record_aad(self._prefix_sha, prefix), tag)
        self._write_cipher(prefix)
        self._write_cipher(cipher)
        if tag == 0:
            self._records += 1

    def write(self, raw):
        try:
            self._live()
            require(isinstance(raw, (bytes, bytearray, memoryview)))
            view = memoryview(raw).cast("B")
            count = len(view)
            require(self._count + count <= self.expected.plaintext_length)
            self._hash.update(view)
            self._count += count
            offset = 0
            while offset < count:
                take = min(wire.CHUNK_BYTES - len(self._buffer), count - offset)
                self._buffer.extend(view[offset:offset+take])
                offset += take
                if len(self._buffer) == wire.CHUNK_BYTES:
                    self._frame(bytes(self._buffer), 0)
                    self._buffer.clear()
            return count
        except BaseException as error:
            self._poison()
            _raise(error)

    def finish(self, receipt):
        try:
            self._live()
            require(type(receipt) is dict and self._count == self.expected.plaintext_length)
            actual = self._hash.hexdigest()
            if self.expected.kind == "central-archive":
                require(receipt.keys() == {"status", "file_count", "blob_count", "archive_bytes",
                                           "archive_sha256", "manifest_sha256", "registry_sha256"})
                require(receipt["status"] == "closure_files_verified")
                validate_int(receipt["file_count"], 100000, 7)
                validate_int(receipt["blob_count"], 99993)
                require(receipt["file_count"] == 7 + receipt["blob_count"])
                count, digest, plan = receipt["archive_bytes"], receipt["archive_sha256"], receipt["manifest_sha256"]
            else:
                require(receipt.keys() == {"status", "plaintext_length", "plaintext_sha256", "plan_sha256", "registry_sha256"})
                require(receipt["status"] == "context_plan_verified" and actual == self.expected.plan_sha256)
                count, digest, plan = receipt["plaintext_length"], receipt["plaintext_sha256"], receipt["plan_sha256"]
            require(type(count) is int and count == self._count and type(digest) is str and digest == actual
                    and plan == self.expected.plan_sha256 and receipt["registry_sha256"] == self.expected.registry_sha256)
            if self._buffer:
                self._frame(bytes(self._buffer), 0)
                self._buffer.clear()
            final = dict(plaintext_length=self._count, plaintext_sha256=actual,
                         plan_sha256=self.expected.plan_sha256, registry_sha256=self.expected.registry_sha256,
                         data_records=self._records)
            self._frame(wire.encode_final(final), 3)
            self._finished = True
            self._state = None
            return {**final, "ciphertext_bytes": self._cipher_count, "ciphertext_sha256": self._cipher_hash.hexdigest()}
        except BaseException as error:
            self._poison()
            _raise(error)

    def close(self):
        self._closed = True
        self._buffer.clear()
        self._state = None


class V2Decoder:
    def __init__(self, private_pem, cipher_source, expected):
        self._buffer = b""
        self._offset = 0
        self._failed = self._closed = self._verified = False
        try:
            _constants()
            self.expected, self._source = _expected(expected), cipher_source
            key, recipient = _pem(private_pem, private=True)
            require(recipient == self.expected.recipient_sha256)
            self._count = self._cipher_count = self._records = 0
            self._hash, self._cipher_hash = hashlib.sha256(), hashlib.sha256()
            magic = self._read_exact(8)
            require(magic == wire.MAGIC)
            size = self._read_exact(4)
            length = struct.unpack(">I", size)[0]
            require(1 <= length <= wire.MAX_HEADER)
            raw = self._read_exact(length)
            header = wire.decode_header(raw, rsa_bytes=(key.key_size + 7) // 8)
            for field in ("backup_id", "kind", "target", "recipient_sha256", "plaintext_length"):
                require(header[field] == getattr(self.expected, field))
            secret = key.decrypt(wire.decode_b64(header["wrapped_key"]), _oaep())
            require(len(secret) == 32)
            self._state = sodium.crypto_secretstream_xchacha20poly1305_state()
            sodium.crypto_secretstream_xchacha20poly1305_init_pull(self._state,
                wire.decode_b64(header["stream_header"], 24), secret)
            self._prefix_sha = hashlib.sha256(magic + size + raw).digest()
        except BaseException as error:
            self._poison()
            _raise(error)

    @property
    def buffered_plaintext(self):
        return len(self._buffer) - self._offset

    @property
    def verified(self):
        return self._verified and not self._failed

    @property
    def receipt(self):
        require(self.verified)
        return {**self._final, "ciphertext_bytes": self._cipher_count, "ciphertext_sha256": self._cipher_hash.hexdigest()}

    def _poison(self):
        self._failed = True
        self._buffer = b""
        self._offset = 0
        self._state = None

    def _read_exact(self, size):
        require(type(size) is int and size > 0)
        require(self._cipher_count + size <= wire.cipher_budget(self.expected.kind))
        raw = bytearray()
        while len(raw) < size:
            piece = self._source.read(size - len(raw))
            require(isinstance(piece, (bytes, bytearray, memoryview)))
            view = memoryview(piece).cast("B")
            require(0 < len(view) <= size - len(raw))
            self._cipher_hash.update(view)
            self._cipher_count += len(view)
            raw.extend(view)
        return bytes(raw)

    def _next(self):
        prefix = self._read_exact(12)
        sequence, length = struct.unpack(">QI", prefix)
        require(sequence == self._records)
        remaining = self.expected.plaintext_length - self._count
        if remaining:
            wanted = min(wire.CHUNK_BYTES, remaining)
            require(length == wanted + wire.ABYTES)
        else:
            require(wire.ABYTES < length <= wire.MAX_FINAL + wire.ABYTES)
        cipher = self._read_exact(length)
        plain, tag = sodium.crypto_secretstream_xchacha20poly1305_pull(
            self._state, cipher, wire.record_aad(self._prefix_sha, prefix))
        if remaining:
            require(tag == 0 and len(plain) == wanted)
            self._hash.update(plain)
            self._count += len(plain)
            self._records += 1
            self._buffer, self._offset = plain, 0
            return
        require(tag == 3)
        final = wire.decode_final(plain)
        require(final["plaintext_length"] == self._count == self.expected.plaintext_length
                and final["plaintext_sha256"] == self._hash.hexdigest()
                and final["plan_sha256"] == self.expected.plan_sha256
                and final["registry_sha256"] == self.expected.registry_sha256
                and final["data_records"] == self._records)
        if self.expected.kind == "central-context":
            require(final["plaintext_sha256"] == self.expected.plan_sha256)
        trailing = self._source.read(1)
        require(isinstance(trailing, (bytes, bytearray, memoryview)) and len(trailing) == 0)
        self._final = final
        self._verified = True
        self._state = None

    def read(self, size):
        try:
            require(not self._closed and not self._failed and type(size) is int and size > 0)
            if self._offset == len(self._buffer):
                self._buffer, self._offset = b"", 0
                if not self._verified:
                    self._next()
            if self._verified:
                return b""
            end = min(len(self._buffer), self._offset + size)
            raw = self._buffer[self._offset:end]
            self._offset = end
            return raw
        except BaseException as error:
            self._poison()
            _raise(error)

    def fileno(self):
        raise io.UnsupportedOperation()

    def close(self):
        self._closed = True
        self._buffer = b""
        self._offset = 0
        self._state = None
