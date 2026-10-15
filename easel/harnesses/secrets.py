"""Redact child-only credentials, including incremental native deltas."""


class StreamRedactor:
    def __init__(self, secrets, emit, additional=None):
        self.additional = additional
        self.secrets = tuple(
            sorted(
                set(v for v in secrets if isinstance(v, str) and v),
                key=len,
                reverse=True,
            )
        )
        self.output = emit
        self.pending = {}

    def _refresh(self):
        if self.additional is not None:
            self.secrets = tuple(
                sorted(
                    set(self.secrets).union(self.additional()), key=len, reverse=True
                )
            )

    def text(self, value):
        self._refresh()
        value = str(value)
        for secret in self.secrets:
            value = value.replace(secret, "[已隐藏]")
        return value

    def value(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, dict):
            return {key: self.value(item) for key, item in value.items()}
        return value

    def emit(self, kind, text):
        if kind not in ("token", "thinking"):
            self.output(kind, self.text(text))
            return
        value = self.text(self.pending.pop(kind, "") + str(text))
        hold = 0
        for secret in self.secrets:
            for size in range(1, min(len(secret), len(value) + 1)):
                if value.endswith(secret[:size]):
                    hold = max(hold, size)
        if hold:
            self.pending[kind] = value[-hold:]
            value = value[:-hold]
        if value:
            self.output(kind, value)

    def flush(self):
        # An incomplete credential prefix is private too; never flush it verbatim.
        for kind in self.pending:
            self.output(kind, "[已隐藏]")
        self.pending.clear()


def private_run(run):
    """Keep all native events/questions/errors inside one transient context."""
    from functools import wraps
    from .base import HarnessError

    @wraps(run)
    async def protected(self, request, emit, ask, bind):
        from easel.platform.privacy import remember, sensitive_values

        values = [
            value
            for key, value in request.env.items()
            if any(
                marker in key.upper()
                for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD")
            )
        ]
        sensitive_values(request.cwd)  # includes the pair before preparation refresh
        remember(request.cwd, values)
        redactor = StreamRedactor(
            values, emit, additional=lambda: sensitive_values(request.cwd)
        )

        async def safe_ask(questions):
            return await ask(redactor.value(questions))

        try:
            return await run(self, request, redactor.emit, safe_ask, bind)
        except HarnessError as error:
            raise HarnessError(redactor.text(str(error))) from None
        finally:
            redactor.flush()

    return protected
