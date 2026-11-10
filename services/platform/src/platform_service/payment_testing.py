"""Explicit loopback-only simulated provider. Never used with live merchant keys."""

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import hmac
import json
import os
import stat
import tempfile
from uuid import uuid4

from .errors import PlatformError
from .payment_config import read_file
from .payments import Checkout, VerifiedPayment, VerifiedRefund
from .payments.contracts import parse_json


class TestPaymentProvider:
    __test__ = False

    def __init__(self, channel, config, *, now=None):
        if (
            channel not in {"wechat", "alipay"}
            or config.mode != "test"
            or len(config.test_key) < 32
            or config.test_state_file is None
        ):
            raise PlatformError(503, "test_payments_not_configured")
        self.channel, self.config = channel, config
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.merchant_id = "test-" + channel + "-merchant"
        self.app_id = "test-" + channel + "-app"

    @contextmanager
    def _state(self, *, write=False):
        path = self.config.test_state_file
        descriptor = None
        try:
            if path.is_symlink():
                raise ValueError
            descriptor = os.open(
                str(path) + ".lock",
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
            )
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            state = (
                json.loads(read_file(path, limit=1048576))
                if path.exists()
                else {"orders": {}}
            )
            if (
                not isinstance(state, dict)
                or set(state) != {"orders"}
                or not isinstance(state["orders"], dict)
            ):
                raise ValueError
            yield state
            if write:
                raw = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
                if len(raw) > 1048576:
                    raise ValueError
                fd, name = tempfile.mkstemp(
                    dir=path.parent, prefix="test-payment-", suffix=".tmp"
                )
                try:
                    with os.fdopen(fd, "wb") as output:
                        output.write(raw)
                        output.flush()
                        os.fsync(output.fileno())
                    os.chmod(name, 0o600)
                    os.replace(name, path)
                finally:
                    if os.path.exists(name):
                        os.unlink(name)
        except (OSError, ValueError, TypeError):
            raise PlatformError(503, "test_provider_state_invalid") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _event(
        self,
        order,
        *,
        status,
        event_id,
        transaction_id=None,
        refund=None,
        occurred_at=None,
    ):
        payload = dict(
            channel=self.channel,
            mode="test",
            event_id=event_id,
            order_id=order.id,
            provider_transaction_id=transaction_id if status != "pending" else None,
            status=status,
            amount_minor=order.amount_minor,
            currency=order.currency,
            merchant_id=self.merchant_id,
            app_id=self.app_id,
            occurred_at=occurred_at or self.now(),
            raw_digest="",
            raw_status="SIMULATED",
        )
        if refund is not None:
            payload.update(
                refund_id=refund.id,
                provider_refund_id="test-refund-" + refund.id,
                refund_amount_minor=refund.amount_minor,
            )
        encoded = json.dumps(
            {
                key: value.isoformat() if isinstance(value, datetime) else value
                for key, value in payload.items()
            },
            sort_keys=True,
        ).encode()
        payload["raw_digest"] = hashlib.sha256(encoded).hexdigest()
        return (
            VerifiedRefund(**payload)
            if refund is not None
            else VerifiedPayment(**payload)
        )

    def record(self, order, *, status, event_id, refund=None, transaction_id=None):
        if (
            status not in {"paid", "closed", "refunded", "refund_failed"}
            or not isinstance(event_id, str)
            or not 1 <= len(event_id) <= 128
        ):
            raise PlatformError(422, "invalid_test_payment_event")
        if (status.startswith("refund")) != (refund is not None):
            raise PlatformError(422, "invalid_test_payment_event")
        transaction_id = transaction_id or "test-" + order.id
        with self._state(write=True) as state:
            previous = state["orders"].get(order.id, {})
            if (
                previous.get("channel", self.channel) != self.channel
                or previous.get("transaction_id", transaction_id) != transaction_id
            ):
                raise PlatformError(409, "test_event_conflict")
            occurred = self.now()
            entry = {
                **previous,
                "channel": self.channel,
                "amount_minor": order.amount_minor,
                "transaction_id": transaction_id,
            }
            if refund is None:
                if previous.get("status") == "paid" and status == "closed":
                    raise PlatformError(409, "test_event_conflict")
                if (
                    previous.get("event_id") == event_id
                    and previous.get("status") == status
                ):
                    occurred = datetime.fromisoformat(previous["occurred_at"])
                entry.update(
                    status=status, event_id=event_id, occurred_at=occurred.isoformat()
                )
            else:
                if previous.get("status") != "paid":
                    raise PlatformError(409, "test_event_conflict")
                entries = dict(previous.get("refunds", {}))
                old = entries.get(refund.id, {})
                if old.get("status") == "refunded" and status != "refunded":
                    raise PlatformError(409, "test_event_conflict")
                if old.get("event_id") == event_id and old.get("status") == status:
                    occurred = datetime.fromisoformat(old["occurred_at"])
                entries[refund.id] = {
                    "status": status,
                    "event_id": event_id,
                    "occurred_at": occurred.isoformat(),
                }
                entry["refunds"] = entries
            state["orders"][order.id] = entry
        event = self._event(
            order,
            status=status,
            event_id=event_id,
            transaction_id=transaction_id,
            refund=refund,
            occurred_at=occurred,
        )
        raw = json.dumps(
            event.to_event(), sort_keys=True, separators=(",", ":")
        ).encode()
        return raw, {
            "X-Easel-Test-Signature": hmac.new(
                self.config.test_key, raw, hashlib.sha256
            ).hexdigest()
        }

    def verify_notification(self, raw, headers):
        supplied = {key.lower(): value for key, value in headers.items()}.get(
            "x-easel-test-signature", ""
        )
        if (
            not isinstance(raw, bytes)
            or len(raw) > 65536
            or not isinstance(supplied, str)
            or not hmac.compare_digest(
                supplied,
                hmac.new(self.config.test_key, raw, hashlib.sha256).hexdigest(),
            )
        ):
            raise PlatformError(400, "payment_invalid_signature")
        payload = parse_json(raw)
        try:
            if (
                payload["channel"] != self.channel
                or payload["mode"] != "test"
                or payload["merchant_id"] != self.merchant_id
                or payload["app_id"] != self.app_id
            ):
                raise ValueError
            if (
                payload["status"] not in {"paid", "closed", "refunded", "refund_failed"}
                or type(payload["amount_minor"]) is not int
                or payload["currency"] != "CNY"
            ):
                raise ValueError
            payload["occurred_at"] = datetime.fromisoformat(payload["occurred_at"])
            if payload["occurred_at"].tzinfo is None:
                raise ValueError
            payload["raw_digest"] = hashlib.sha256(raw).hexdigest()
            return (
                VerifiedRefund(**payload)
                if "refund_id" in payload
                else VerifiedPayment(**payload)
            )
        except (KeyError, ValueError, TypeError):
            raise PlatformError(400, "invalid_test_payment_event") from None

    async def create(self, order):
        return Checkout(
            "easel-test-payment://" + self.channel + "/" + order.id, order.expires_at
        )

    async def query(self, order):
        with self._state() as state:
            entry = state["orders"].get(order.id, {})
        if entry and (
            entry["channel"] != self.channel
            or entry["amount_minor"] != order.amount_minor
        ):
            raise PlatformError(409, "test_event_conflict")
        status = entry.get("status", "pending")
        return self._event(
            order,
            status=status,
            event_id="query-" + entry.get("event_id", uuid4().hex),
            transaction_id=entry.get("transaction_id"),
            occurred_at=datetime.fromisoformat(entry["occurred_at"])
            if entry.get("occurred_at")
            else None,
        )

    async def close(self, order):
        fact = await self.query(order)
        if fact.status == "pending":
            self.record(order, status="closed", event_id="test-close-" + order.id)
        return await self.query(order)

    async def refund(self, order, refund):
        return await self.query_refund(order, refund)

    async def query_refund(self, order, refund):
        with self._state() as state:
            entry = state["orders"].get(order.id, {})
        observed = entry.get("refunds", {}).get(refund.id, {})
        return self._event(
            order,
            status=observed.get("status", "refund_pending"),
            event_id="query-" + observed.get("event_id", uuid4().hex),
            transaction_id=entry.get("transaction_id"),
            refund=refund,
            occurred_at=datetime.fromisoformat(observed["occurred_at"])
            if observed.get("occurred_at")
            else None,
        )
