"""Single-use redemption codes (decision 0013, migration 026).

Codes are 16 characters from Crockford's base32 (about 80 bits), shown once to the
admin who created them and stored only as SHA-256 hashes.
"""
import hashlib
import re
import secrets

import psycopg

from .errors import PlatformError

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_LOOKALIKES = str.maketrans({"I": "1", "L": "1", "O": "0"})
ERRORS = {
    "EB403": (403, "account_unavailable"),
    "EB404": (404, "redemption_code_invalid"),
    "EB409": (409, "redemption_code_used"),
    "EB410": (409, "redemption_code_disabled"),
    "EB411": (409, "redemption_code_expired"),
    "EB412": (409, "redemption_batch_already_used"),
    "EB413": (409, "wallet_capacity_exceeded"),
}


def new_code():
    raw = "".join(secrets.choice(ALPHABET) for _ in range(16))
    return "-".join(raw[i:i + 4] for i in range(0, 16, 4))


def normalize(code):
    if not isinstance(code, str) or len(code) > 64:
        raise PlatformError(404, "redemption_code_invalid")
    value = re.sub(r"[\s-]", "", code).upper().translate(_LOOKALIKES)
    if len(value) != 16 or any(char not in ALPHABET for char in value):
        raise PlatformError(404, "redemption_code_invalid")
    return value


def code_hash(code):
    return hashlib.sha256(normalize(code).encode()).hexdigest()


class RedemptionService:
    def __init__(self, db):
        self.db = db

    async def redeem(self, scope, code):
        digest = code_hash(code)
        try:
            async with self.db.connection() as conn:
                await conn.execute("SET LOCAL statement_timeout='10s'")
                row = await (await conn.execute("SELECT redeem_code(%s,%s,%s) AS result",
                                                (scope.user_id, scope.billing_account_id, digest))).fetchone()
                return row["result"]
        except psycopg.Error as error:
            failure = ERRORS.get(getattr(error, "sqlstate", None))
            if failure is None:
                raise
            raise PlatformError(*failure) from None
