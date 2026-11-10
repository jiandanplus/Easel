"""Whether the signed-in account has an active central subscription.

The AI 服务商 rule: with an active subscription every task uses it; without one, tasks use the
user's own configured providers. Asking central takes a network round trip, so the answer is
refreshed in a background thread and callers read the last known value without waiting.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

REFRESH_SECONDS = 60


def check(root: Path) -> bool:
    """True when signed in and an active membership includes text (chat)."""
    from easel.platform.central import CentralClient
    client = CentralClient(root)
    if not client.status(verify=False).get("authenticated"):
        return False
    entitlements = client.call("GET", "/v2/products/easel/entitlements")
    return bool(entitlements.get("active")) and "text" in (entitlements.get("features") or [])


class SubscriptionState:
    def __init__(self, root: Path, *, checker=check, clock=time.monotonic):
        self.root, self._checker, self._clock = Path(root), checker, clock
        self._value: bool | None = None
        self._checked = float("-inf")
        self._lock = threading.Lock()
        self._running = False

    def active(self) -> bool | None:
        """The last known answer; None until the first check has finished."""
        if self._clock() - self._checked > REFRESH_SECONDS:
            self._start()
        return self._value

    def refresh(self) -> None:
        """Ask again now, e.g. after sign-in, sign-out or a redeemed code."""
        self._checked = float("-inf")
        self._start()

    def _start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
        threading.Thread(target=self._run, name="easel-subscription-state", daemon=True).start()

    def _run(self) -> None:
        try:
            value = bool(self._checker(self.root))
        except Exception:  # Network or sign-in trouble keeps the last known answer.
            value = self._value
        self._value = value
        self._checked = self._clock()
        with self._lock:
            self._running = False
