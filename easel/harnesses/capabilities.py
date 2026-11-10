"""Shared engine/source capabilities; legacy routes remain explicit."""

RUNTIMES = ('pi', 'api', 'openclaw', 'opencode', 'codex', 'acp')
_SUBSCRIPTIONS = {'pi': ('central',), 'codex': ('central', 'legacy')}


def subscription_backends(runtime: str) -> list[str]:
    return list(_SUBSCRIPTIONS.get(runtime, ()))


def supports_subscription(runtime: str, backend: str) -> bool:
    return backend in _SUBSCRIPTIONS.get(runtime, ())
