"""Supplier keys typed into the console.

The console holds only a public key, so it can seal a key but never read one back.
Only the central service holds the private key. The sealed text also names the channel
and address it was typed for, so a changed database row cannot send a key elsewhere.
"""
import base64
import json
import os
import stat
import sys

from nacl.exceptions import CryptoError
from nacl.public import PrivateKey, PublicKey, SealedBox


def _decode(value):
    raw = base64.b64decode(value, validate=True)
    if len(raw) != 32:
        raise ValueError
    return raw


def valid_public_key(value):
    try:
        _decode(value)
    except (ValueError, TypeError):
        return False
    return True


def seal(public_key, channel, base_url, secret):
    plain = json.dumps([channel, base_url, secret], separators=(",", ":")).encode()
    return base64.b64encode(SealedBox(PublicKey(_decode(public_key))).encrypt(plain)).decode()


class ProviderKeys:
    def __init__(self, private_key):
        self._box = SealedBox(private_key)

    def open(self, sealed, channel, base_url):
        """The key, or None when the row is damaged or was not sealed for this address."""
        try:
            value = json.loads(self._box.decrypt(base64.b64decode(sealed, validate=True)))
        except (CryptoError, ValueError, TypeError):
            return None
        if not isinstance(value, list) or len(value) != 3 or value[:2] != [channel, base_url] or not isinstance(value[2], str):
            return None
        return value[2]

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        path = env.get("CENTRAL_PROVIDER_KEY_FILE")
        if not path:
            return None
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if (not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600
                        or info.st_uid != os.getuid() or info.st_size > 256):
                    raise ValueError
                return cls(PrivateKey(_decode(source.read(257).strip())))
        except (OSError, ValueError, TypeError):
            raise ValueError("invalid_provider_key_file") from None


def generate(path):
    """Create the private key file and return the public key for the console."""
    key = PrivateKey.generate()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as target:
        target.write(base64.b64encode(bytes(key)))
    return base64.b64encode(bytes(key.public_key)).decode()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m platform_service.provider_secrets <new private key file>")
    print(generate(sys.argv[1]))
