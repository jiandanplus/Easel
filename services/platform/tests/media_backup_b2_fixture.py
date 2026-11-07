"""Synthetic-only crypto and bounded-I/O fixtures; no deployment material."""
import base64
from functools import lru_cache
import hashlib
import io

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

BACKUP_ID = "12345678-1234-4234-9234-123456789abc"
TARGET = "synthetic-deployment"


@lru_cache(maxsize=1)
def synthetic_keys():
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return public, private, hashlib.sha256(der).hexdigest()


def header():
    return dict(version=2, algorithm="RSA-OAEP-SHA256+SECRETSTREAM-XCHACHA20-POLY1305",
                backup_id=BACKUP_ID, kind="central-context", target=TARGET,
                recipient_sha256="a" * 64, plaintext_length=19, chunk_bytes=1048576,
                wrapped_key=base64.b64encode(b"w" * 384).decode(),
                stream_header=base64.b64encode(b"h" * 24).decode())


def final():
    return dict(plaintext_length=19, plaintext_sha256="b" * 64,
                plan_sha256="b" * 64, registry_sha256="c" * 64, data_records=1)


def commit():
    return dict(format="easel-backup-commit-v2", backup_id=BACKUP_ID, target=TARGET,
                window_id=BACKUP_ID, epoch=0, plan_sha256="b" * 64, registry_sha256="c" * 64,
                objects={"archive": {"name": "archive.easelbs2", "bytes": 2000, "sha256": "d" * 64},
                         "context": {"name": "context.easelbs2", "bytes": 1800, "sha256": "e" * 64}})


def small_plan():
    from media_backup.contract import encode_manifest, registry_digest, validate_plan
    names = ("db/central.dump", "db/identity.dump", "db/central.roles", "db/identity.roles",
             "config/central", "config/identity", "config/media")
    value = dict(format="easel-media-closure-v1", snapshots=dict(central="central-synthetic",
                 identity="identity-synthetic", source_sha256="a" * 64, schema_sha256="b" * 64),
                 files=[dict(name=name, size=0, sha256=hashlib.sha256(b"").hexdigest(),
                             uid=0, gid=0, mode=0o600) for name in names],
                 blobs=[], references=[], registry_sha256=registry_digest([], []))
    plan = validate_plan(value)
    return plan, encode_manifest(plan)


class ShortReader(io.BytesIO):
    def __init__(self, raw, maximum=7):
        super().__init__(raw)
        self.maximum = maximum
        self.requests = []

    def read(self, size):
        self.requests.append(size)
        assert type(size) is int and size > 0
        return super().read(min(size, self.maximum))


class ShortWriter(io.BytesIO):
    def __init__(self, maximum=11):
        super().__init__()
        self.maximum = maximum
        self.requests = []

    def write(self, raw):
        self.requests.append(len(raw))
        return super().write(raw[:self.maximum])


# Public synthetic test-only key and locally generated independent v2 vector.
# Never deployment material; tests are excluded from the application wheel.
KNOWN_V2_PRIVATE_PEM = b'-----BEGIN PRIVATE KEY-----\n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\nXhN4mBu8qcfAKzl0RX+bnQ==\n-----END PRIVATE KEY-----\n'
KNOWN_V2_CIPHERTEXT_B64 = 'RUFTRUxCUwIAAAN9eyJhbGdvcml0aG0iOiJSU0EtT0FFUC1TSEEyNTYrU0VDUkVUU1RSRUFNLVhDSEFDSEEyMC1QT0xZMTMwNSIsImJhY2t1cF9pZCI6IjEyMzQ1Njc4LTEyMzQtNDIzNC05MjM0LTEyMzQ1Njc4OWFiYyIsImNodW5rX2J5dGVzIjoxMDQ4NTc2LCJraW5kIjoiY2VudHJhbC1jb250ZXh0IiwicGxhaW50ZXh0X2xlbmd0aCI6MzksInJlY2lwaWVudF9zaGEyNTYiOiJjMzNmNDA0NGFjN2I5ZWI4NWZjYzg3N2ZlYTkxNTRlOGI1ZjBjZmE2MmQ5ODkyZTliMTlkNGNiYTliNDhmZjFjIiwic3RyZWFtX2hlYWRlciI6Imxyc1VYK2ZWVTdlUnFlMzZnRGdoTE80VUtzSWI1NmVPIiwidGFyZ2V0Ijoic3ludGhldGljLWRlcGxveW1lbnQiLCJ2ZXJzaW9uIjoyLCJ3cmFwcGVkX2tleSI6ImxUQkVIbmV5WDhTZjJuc2k1UStCeWxiZzdFVEdycE95NGx1Y21Nd3BaMDZXWEFDQlhXQzZJN0RUL3p5VENMTG9KRHRZYnNsOVFFRTJTekorMHNTSmliYlp2Rkp5NEU4N2puSndnU3ovM01nNGFJOTIrczdMWFFzeXhMZzZ4SUhST1pPRlRpSnBGTlNYTDQvK0dWajd6QXp6UXpJRkcyRzluSzlSeTFKM05BVThveTVWMFE0aEIzbFZDYUswVjFHTjJTOTQ0bmlFMmFQVlBvQWZKM1d5RG1TTGtzVUxDVlVOQTFPakVJVG5HZUFrR2RER3pDaUVCRk5ORUpCNXI3VHRnMUFPWmx1K2lMZlM1M3k0RXVWWWR6L0RYeGtweVhkQkwxbXg3TjFBVzlnbnpmUFhEcjM3bmxxRmZpYVd5WHpyWWNyWEpRWDRpekhIWnhMY2Q5aGVya2Zwbm44emFtQkI2WFpsVXFtYVlKTWY1djZWYUFiNHJSUHhJM3MrZmFhTXhQUWlWTXlXaHlHZGtxSEcwUWV4bUxrYTlwaUNvdm1wT21zV2ZmWEJvNUI5WUFuaFhpR0pjenZhakRLSmF5ak5sODlFcHd4Z3FRRlQyR1dQcGdMSlF0UisrNWowMS8xWHVWOVRGcWJnOUppbHZWNCtidnNmZFJrU2RUV2VNaTA0In0AAAAAAAAAAAAAADgzZAHoMOMZmK7Y62yvEOY38I+Lvf6+Nt2s/nz5wnQow7o94O+J412Zwg3Yu7ahrQakmpuNUb6ZHQAAAAAAAAABAAABNZODqsxZlqlF91OTMtVtoLEsscOs+w2h5O4w8VHZjX2RU/wkbrGu2Ar5BjiI2/7lExtPx7irsPzuwdbZqiUlhm31uWogEMCaJguWkutRhLOCrtJJ0KRW1Psa0zTXCUad/gmXj077n5ejTP/3zRmpMq5hOEp3Zkk/m1P048uqTNCVzbXX6ih2UbXuNKCVFxzbfPnAm0KCder0hreQiBoYFNRp+R1Iuy5mTt8qRtdo+d79pHeJbspPXD/6wb6h+eeRQK8RhYWbmjZQ0X3zDsFLDcl7ORza9vdFoplt0AbD0HkTLPb701xKFJO5nFzjwdxex9ZDYLCAl7hEzTFDlfDlS5hxR8bxUJzeg9pnlC/PShWOZztjf3J0icr07zur47Es4adjVHj73wzp37gV2Xn61/QTHBaRng=='
KNOWN_V2_CIPHERTEXT_SHA256 = 'a77724199c71efa794b7b4e68fc79c35b5ae2b3792db397bf62ce7e3cbcb48af'
KNOWN_V2_RECIPIENT_SHA256 = 'c33f4044ac7b9eb85fcc877fea9154e8b5f0cfa62d9892e9b19d4cba9b48ff1c'
KNOWN_V2_PLAINTEXT = b'synthetic fixed v2 known-answer context'
