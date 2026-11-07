import hashlib
import hmac
import secrets
import unicodedata

class PlatformError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code
        super().__init__(code)

def password_valid(value):
    return isinstance(value, str) and 15 <= len(value) <= 128 and not any(unicodedata.category(c).startswith("C") for c in value) and len(value.encode("utf-8")) <= 512

def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()

def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    value = hashlib.scrypt(password.encode(), salt=salt, n=131072, r=8, p=1, dklen=32, maxmem=256*1024*1024)
    return "scrypt$131072$8$1$" + salt.hex() + "$" + value.hex()

def password_matches(password, stored):
    return hmac.compare_digest(password_hash(password, bytes.fromhex(stored.split("$")[4])), stored)
