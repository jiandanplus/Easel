"""Exact public dependency pins and runtime evidence; no backup/config imports.

An API probe is deliberately not an overall T0 acceptance receipt. The loaded
wheel has to be bound independently to publisher/vendor provenance and target
ABI evidence. In particular, stripped static wheels may expose no version API.
"""
from dataclasses import dataclass
from email.parser import BytesParser
import ctypes
import hashlib
import io
from importlib import metadata
from pathlib import Path
import platform
import sys
from types import MappingProxyType
import zipfile


class DependencyError(ValueError):
    def __init__(self):
        super().__init__("invalid_dependency")


@dataclass(frozen=True)
class _WheelPin:
    filename: str
    sha256: str
    tag: str
    native_sha256: str


_PINS = MappingProxyType({
    "macos-universal2": _WheelPin(
        "pynacl-1.6.2-cp38-abi3-macosx_10_10_universal2.whl",
        "c949ea47e4206af7c8f604b8278093b674f7c79ed0d4719cc836902bf4517465",
        "cp38-abi3-macosx_10_10_universal2",
        "49032c357199dde6ae341319e558ce3ed9daa80f5ac83972aa9a63158b0b5371",
    ),
    "linux-x86_64": _WheelPin(
        "pynacl-1.6.2-cp38-abi3-manylinux_2_34_x86_64.whl",
        "c8a231e36ec2cab018c4ad4358c386e36eede0319a0c41fed24f840b1dac59f6",
        "cp38-abi3-manylinux_2_34_x86_64",
        "554206748472a39920ad95a26b6c217ce65439fd58d755a0f05457b3cbfbb57f",
    ),
})
_LICENSE_HASHES = MappingProxyType({
    "PyNaCl": "d3174ad63e721d4c9dccb8ad4320848992d314369bc46319720b5802c9153fe9",
    "libsodium": "508a76d186356c0dd807a670ef510964f8724557024796a2c426c6c0e19ab683",
})
_LICENSE_MEMBERS = {
    "PyNaCl": "pynacl-1.6.2.dist-info/licenses/LICENSE",
    "libsodium": "pynacl-1.6.2.dist-info/licenses/licenses/LICENSE.libsodium.txt",
}
_CONSTANTS = {"KEYBYTES": 32, "HEADERBYTES": 24, "ABYTES": 17,
              "TAG_MESSAGE": 0, "TAG_PUSH": 1, "TAG_REKEY": 2, "TAG_FINAL": 3}
_PREFIX = "crypto_secretstream_xchacha20poly1305_"


def _require(condition):
    if not condition:
        raise DependencyError()


def verify_wheel(path, *, target):
    """Validate the exact reviewed wheel, including its native/license members."""
    try:
        pin = _PINS[target]
        path = Path(path)
        _require(path.name == pin.filename and path.is_file() and not path.is_symlink())
        _require(path.stat().st_size <= 4 * 1024 * 1024)
        raw = path.read_bytes()
        _require(hashlib.sha256(raw).hexdigest() == pin.sha256)
        with zipfile.ZipFile(io.BytesIO(raw)) as wheel:
            names = wheel.namelist()
            _require(len(names) == len(set(names)))
            _require(all(member.file_size <= 20 * 1024 * 1024 for member in wheel.infolist()))
            package = BytesParser().parsebytes(wheel.read("pynacl-1.6.2.dist-info/METADATA"))
            _require(package.get_all("Name") == ["PyNaCl"] and package.get_all("Version") == ["1.6.2"])
            info = BytesParser().parsebytes(wheel.read("pynacl-1.6.2.dist-info/WHEEL"))
            _require(info.get_all("Tag") == [pin.tag])
            licenses = {name: hashlib.sha256(wheel.read(member)).hexdigest()
                        for name, member in _LICENSE_MEMBERS.items()}
            _require(licenses == dict(_LICENSE_HASHES))
            native = hashlib.sha256(wheel.read("nacl/_sodium.abi3.so")).hexdigest()
            _require(native == pin.native_sha256)
        return {"verified": True, "version": "1.6.2", "filename": pin.filename,
                "sha256": pin.sha256, "bytes": len(raw), "tag": pin.tag,
                "native_sha256": native, "license_sha256": licenses}
    except DependencyError:
        raise
    except (OSError, KeyError, ValueError, TypeError, zipfile.BadZipFile):
        raise DependencyError() from None


def _target():
    if sys.platform == "darwin" and platform.machine() in {"arm64", "x86_64"}:
        return "macos-universal2"
    if sys.platform == "linux" and platform.machine() == "x86_64":
        return "linux-x86_64"
    raise DependencyError()


def _version_api(native):
    # The exact official wheels statically link/strip libsodium. A missing symbol
    # is an explicit unavailable measurement, never inferred from package version.
    try:
        function = ctypes.CDLL(str(native)).sodium_version_string
        function.argtypes = []
        function.restype = ctypes.c_char_p
        value = function()
        _require(isinstance(value, bytes) and 1 <= len(value) <= 64)
        return value.decode("ascii")
    except (AttributeError, OSError):
        return None


def probe_runtime():
    """Exercise the loaded, hash-pinned native code without real keys or IO."""
    try:
        import nacl
        import nacl._sodium
        import nacl.bindings as bindings
        from nacl.exceptions import CryptoError

        _require(sys.implementation.name == "cpython" and sys.version_info[:2] == (3, 12))
        versions = {name: metadata.version(name) for name in ("PyNaCl", "cffi", "cryptography")}
        _require(versions == {"PyNaCl": "1.6.2", "cffi": "2.1.1", "cryptography": "50.0.2"})
        _require(nacl.__version__ == "1.6.2")
        for suffix in ("state", "keygen", "init_push", "init_pull", "push", "pull", "rekey"):
            _require(callable(getattr(bindings, _PREFIX + suffix, None)))
        for suffix, value in _CONSTANTS.items():
            actual = getattr(bindings, _PREFIX + suffix, None)
            _require(type(actual) is int and actual == value)
        target = _target()
        native = Path(nacl._sodium.__file__)
        _require(native.stat().st_size <= 20 * 1024 * 1024)
        native_sha = hashlib.sha256(native.read_bytes()).hexdigest()
        _require(native_sha == _PINS[target].native_sha256)
        installed = metadata.distribution("PyNaCl")
        licenses = {name: hashlib.sha256(installed.locate_file(member).read_bytes()).hexdigest()
                    for name, member in _LICENSE_MEMBERS.items()}
        _require(licenses == dict(_LICENSE_HASHES))
        state = getattr(bindings, _PREFIX + "state")
        key = getattr(bindings, _PREFIX + "keygen")()
        _require(len(key) == 32)
        push_state = state()
        header = getattr(bindings, _PREFIX + "init_push")(push_state, key)
        _require(len(header) == 24)
        push, pull = getattr(bindings, _PREFIX + "push"), getattr(bindings, _PREFIX + "pull")
        message = b"B2 public dependency self-test"
        cipher = push(push_state, message, ad=b"B2-AAD", tag=0)
        final = push(push_state, b"", ad=b"B2-FINAL", tag=3)
        _require(len(cipher) == len(message) + 17 and len(final) == 17)
        pull_state = state()
        getattr(bindings, _PREFIX + "init_pull")(pull_state, header, key)
        _require(pull(pull_state, cipher, ad=b"B2-AAD") == (message, 0))
        _require(pull(pull_state, final, ad=b"B2-FINAL") == (b"", 3))
        rejected_state = state()
        getattr(bindings, _PREFIX + "init_pull")(rejected_state, header, key)
        try:
            pull(rejected_state, cipher, ad=b"wrong-AAD")
        except CryptoError:
            aad_rejected = True
        else:
            raise DependencyError()
        # Exact regression point from the upstream CVE-2025-69277 fix.
        patched = not bindings.crypto_core_ed25519_is_valid_point(bytes.fromhex("95" + "99" * 31))
        _require(patched)
        native_version = _version_api(native)
        return {"api_ready": True, "versions": versions, "target": target,
                "python": platform.python_version(), "machine": platform.machine(),
                "native_sha256": native_sha, "license_sha256": licenses,
                "constants": dict(_CONSTANTS), "final_roundtrip": True,
                "aad_rejected": aad_rejected, "patched_point_rejected": patched,
                "native_version_api_available": native_version is not None,
                "native_version": native_version}
    except DependencyError:
        raise
    except (ImportError, OSError, ValueError, TypeError, AttributeError, metadata.PackageNotFoundError):
        raise DependencyError() from None
