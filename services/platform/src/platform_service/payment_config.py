"""Server-only payment credentials; disabled and test modes fail closed."""

from dataclasses import dataclass, field
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

from .errors import PlatformError


def read_file(path, *, private=True, limit=65536):
    """Read a bounded regular file without following the final symlink."""
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or (private and stat.S_IMODE(info.st_mode) & 0o077)
            or info.st_size > limit
        ):
            raise ValueError
        data = os.read(descriptor, limit + 1)
        if not data or len(data) > limit:
            raise ValueError
        return data
    except (OSError, ValueError, TypeError):
        raise PlatformError(503, "payment_configuration_invalid") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _unique_object(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", value):
        raise ValueError
    return value


@dataclass(frozen=True)
class PaymentSettings:
    mode: str = "disabled"
    wechat: dict = field(default_factory=dict, repr=False)
    alipay: dict = field(default_factory=dict, repr=False)
    test_key: bytes = field(default=b"", repr=False)
    test_state_file: Path | None = field(default=None, repr=False)
    worker_enabled: bool = True

    @property
    def channels(self):
        if self.mode == "test":
            return tuple(
                {
                    "id": key,
                    "label": label + "（模拟）",
                    "merchant_id": "test-" + key + "-merchant",
                    "app_id": "test-" + key + "-app",
                    "enabled": True,
                }
                for key, label in (("wechat", "微信支付"), ("alipay", "支付宝"))
            )
        if self.mode == "live":
            return tuple(
                {
                    "id": key,
                    "label": label,
                    "merchant_id": config["merchant_id"],
                    "app_id": config["app_id"],
                    "enabled": True,
                }
                for key, label, config in (
                    ("wechat", "微信支付", self.wechat),
                    ("alipay", "支付宝", self.alipay),
                )
                if config
            )
        return ()

    @classmethod
    def from_env(cls, service, env=None):
        env = os.environ if env is None else env
        mode = env.get("CENTRAL_PAYMENTS_MODE", "disabled")
        if mode == "disabled":
            return cls()
        try:
            if mode not in {"test", "live"}:
                raise ValueError
            parsed = urlsplit(service.public_url)
            try:
                loopback = ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                loopback = False
            if mode == "test" and not (service.allow_loopback_http and loopback):
                raise ValueError
            if mode == "live" and (
                parsed.scheme != "https" or loopback or service.allow_loopback_http
            ):
                raise ValueError
            path = Path(env["CENTRAL_PAYMENTS_CONFIG_FILE"]).expanduser()
            data = json.loads(read_file(path), object_pairs_hook=_unique_object)
            if not isinstance(data, dict):
                raise ValueError

            def located(value):
                if (
                    not isinstance(value, str)
                    or not value
                    or any(ord(c) < 32 for c in value)
                ):
                    raise ValueError
                target = Path(value).expanduser()
                return target if target.is_absolute() else path.parent / target

            worker = env.get("CENTRAL_PAYMENTS_WORKER", "1")
            if worker not in {"0", "1"}:
                raise ValueError
            if mode == "test":
                if set(data) != {"test_key_file", "test_state_file"}:
                    raise ValueError
                key = read_file(located(data["test_key_file"]))
                if not 32 <= len(key) <= 256:
                    raise ValueError
                target = located(data["test_state_file"])
                if not target.parent.is_dir() or target.is_symlink():
                    raise ValueError
                if target.exists():
                    read_file(target, limit=1048576)
                return cls(
                    mode="test",
                    test_key=key,
                    test_state_file=target,
                    worker_enabled=worker == "1",
                )
            if not data or set(data) - {"wechat", "alipay"}:
                raise ValueError
            wechat, alipay = {}, {}
            for channel in data:
                source = data[channel]
                required = {"merchant_id", "app_id", "merchant_private_key_file"}
                required |= (
                    {"mch_serial", "verification_key_files", "api_v3_key_file"}
                    if channel == "wechat"
                    else {"alipay_public_key_file"}
                )
                if not isinstance(source, dict) or set(source) != required:
                    raise ValueError
                result = {
                    "merchant_id": _identifier(source["merchant_id"]),
                    "app_id": _identifier(source["app_id"]),
                    "mode": "live",
                    "merchant_private_key": read_file(
                        located(source["merchant_private_key_file"])
                    ),
                    "notify_url": service.public_url
                    + "/v2/payments/"
                    + channel
                    + "/notify",
                }
                if channel == "wechat":
                    keys = source["verification_key_files"]
                    if not isinstance(keys, dict) or not 1 <= len(keys) <= 16:
                        raise ValueError
                    api_key = read_file(located(source["api_v3_key_file"]))
                    if len(api_key) != 32:
                        raise ValueError
                    result.update(
                        mch_serial=_identifier(source["mch_serial"]),
                        verification_keys={
                            _identifier(serial): read_file(
                                located(filename), private=False
                            )
                            for serial, filename in keys.items()
                        },
                        api_v3_key=api_key,
                        refund_notify_url=result["notify_url"],
                    )
                    wechat = result
                else:
                    result["alipay_public_key"] = read_file(
                        located(source["alipay_public_key_file"]), private=False
                    )
                    alipay = result
            return cls(
                mode="live", wechat=wechat, alipay=alipay, worker_enabled=worker == "1"
            )
        except PlatformError:
            raise
        except (ValueError, KeyError, TypeError, OSError):
            raise PlatformError(503, "payment_configuration_invalid") from None
