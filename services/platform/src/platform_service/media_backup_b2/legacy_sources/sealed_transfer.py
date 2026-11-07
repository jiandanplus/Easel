"""One-shot bounded authenticated envelope. Pure byte APIs; no I/O or logging.

Wire: MAGIC(8) || header_size(u32 BE) || canonical ASCII JSON || GCM ciphertext+tag.
The complete prefix including wrapped key and nonce is authenticated as GCM AAD.
Only protocol context is visible: never put user identifiers or application metadata
in the header. Caller supplies a fresh UUIDv4 and tracks consumption to reject replay.
RSA public key provenance must be verified through the authenticated transport.
Python cannot guarantee erasure of immutable secret bytes from process memory.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import struct
import uuid

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b'EASELST\x01'
VERSION = 1
ALGORITHM = 'RSA-OAEP-SHA256+A256GCM'
TARGET = 'ser362540173134'
MAX_PAYLOAD = 32 * 1024 * 1024
MAX_HEADER = 4096
MAX_PEM = 32768
_META = frozenset(('transfer_id', 'kind', 'target', 'expected_sha256', 'expected_length'))
_HEADER = _META | {'version', 'algorithm', 'wrapped_key', 'nonce'}


class SealedTransferError(ValueError):
    """Generic failure: no secret or input context is included in the message."""


def _check(condition):
    if not condition:
        raise ValueError()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _check(key not in result)
        result[key] = value
    return result


def _metadata(value):
    _check(type(value) is dict and set(value) == _META)
    _check(type(value['transfer_id']) is str)
    token = uuid.UUID(value['transfer_id'])
    _check(token.version == 4 and token.variant == uuid.RFC_4122
           and str(token) == value['transfer_id'])
    _check(type(value['kind']) is str and value['kind'] in ('central', 'logto'))
    _check(type(value['target']) is str and value['target'] == TARGET)
    _check(type(value['expected_length']) is int
           and 0 <= value['expected_length'] <= MAX_PAYLOAD)
    _check(type(value['expected_sha256']) is str
           and re.fullmatch('[0-9a-f]{64}', value['expected_sha256']) is not None)


def _pem(value):
    _check(type(value) is bytes and 1 <= len(value) <= MAX_PEM)
    # No multiple PEM objects or unparsed trailing material.
    _check(value.count(b'-----BEGIN ') == 1 and value.count(b'-----END ') == 1)
    _check(re.fullmatch(rb'-----BEGIN ([A-Z ]+)-----\r?\n[A-Za-z0-9+/=\r\n]+'
                        rb'-----END \1-----\r?\n?', value) is not None)


def _rsa(key, private=False):
    _check(isinstance(key, rsa.RSAPrivateKey if private else rsa.RSAPublicKey))
    pub = key.public_key() if private else key
    _check(3072 <= pub.key_size <= 8192 and pub.public_numbers().e == 65537)


def _b64(value, length):
    _check(type(value) is str and len(value) <= 1400)
    decoded = base64.b64decode(value, validate=True)
    _check(len(decoded) == length
           and base64.b64encode(decoded).decode('ascii') == value)
    return decoded


def _oaep():
    return padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                        algorithm=hashes.SHA256(), label=MAGIC)


def seal(public_pem, plaintext, metadata):
    """Return an envelope; metadata must contain exactly the five documented keys.

    The caller must generate a fresh UUIDv4 per transfer; no global replay state is
    maintained. Expected SHA256 and length must match plaintext, and are visible
    authenticated protocol fields. Plaintext is never written by this module.
    """
    try:
        _check(type(plaintext) is bytes and len(plaintext) <= MAX_PAYLOAD)
        _metadata(metadata)
        _check(len(plaintext) == metadata['expected_length'])
        _check(hmac.compare_digest(hashlib.sha256(plaintext).hexdigest(),
                                   metadata['expected_sha256']))
        _pem(public_pem)
        public = serialization.load_pem_public_key(public_pem)
        _rsa(public)
        key, nonce = os.urandom(32), os.urandom(12)
        wrapped = public.encrypt(key, _oaep())
        header = dict(metadata, version=VERSION, algorithm=ALGORITHM,
                      wrapped_key=base64.b64encode(wrapped).decode('ascii'),
                      nonce=base64.b64encode(nonce).decode('ascii'))
        raw = _json(header)
        _check(1 <= len(raw) <= MAX_HEADER)
        aad = MAGIC + struct.pack('>I', len(raw)) + raw
        return aad + AESGCM(key).encrypt(nonce, plaintext, aad)
    except Exception:
        raise SealedTransferError('sealed transfer rejected') from None


def unseal(private_pem, envelope, expected_transfer_id, expected_kind, expected_target):
    """Authenticate complete envelope and expected context, then return bytes.

    Fails closed on every parse/crypto/length/hash error. The caller must supply
    independently known context and enforce one-time use; reading context from the
    envelope and passing it back defeats the transfer binding.
    """
    try:
        _check(type(envelope) is bytes
               and 29 <= len(envelope) <= 12 + MAX_HEADER + MAX_PAYLOAD + 16)
        _check(envelope[:8] == MAGIC)
        hlen = struct.unpack('>I', envelope[8:12])[0]
        _check(1 <= hlen <= MAX_HEADER and len(envelope) >= 12 + hlen + 16)
        raw = envelope[12:12+hlen]
        header = json.loads(raw.decode('ascii'), object_pairs_hook=_pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        _check(type(header) is dict and set(header) == _HEADER)
        _check(_json(header) == raw)
        _check(type(header['version']) is int and header['version'] == VERSION)
        _check(type(header['algorithm']) is str and header['algorithm'] == ALGORITHM)
        metadata = {key: header[key] for key in _META}
        _metadata(metadata)
        _check(type(expected_transfer_id) is str
               and type(expected_kind) is str and type(expected_target) is str)
        _check(header['transfer_id'] == expected_transfer_id
               and header['kind'] == expected_kind and header['target'] == expected_target)
        _check(len(envelope) == 12 + hlen + header['expected_length'] + 16)
        _pem(private_pem)
        private = serialization.load_pem_private_key(private_pem, password=None)
        _rsa(private, private=True)
        wrapped = _b64(header['wrapped_key'], private.key_size // 8)
        nonce = _b64(header['nonce'], 12)
        key = private.decrypt(wrapped, _oaep())
        _check(len(key) == 32)
        plaintext = AESGCM(key).decrypt(nonce, envelope[12+hlen:], envelope[:12+hlen])
        _check(len(plaintext) == header['expected_length'])
        _check(hmac.compare_digest(hashlib.sha256(plaintext).hexdigest(),
                                   header['expected_sha256']))
        return plaintext
    except Exception:
        raise SealedTransferError('sealed transfer rejected') from None
