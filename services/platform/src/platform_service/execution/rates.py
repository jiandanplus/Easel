"""Bounded exact rational prices; round the sum once."""

from fractions import Fraction
from ..credits import integer
from ..errors import PlatformError


def monetary(value):
    if (
        isinstance(value, str)
        and value.isascii()
        and value.isdecimal()
        and len(value) <= 19
    ):
        value = int(value)
    return integer(value)


def normalize(rate):
    if not isinstance(rate, dict) or set(rate) != {"version", "input", "output"}:
        raise PlatformError(422, "invalid_rate")
    from ..credits import bounded

    out = {"version": bounded(rate["version"])}
    for kind in ("input", "output"):
        part = rate[kind]
        if not isinstance(part, dict) or set(part) != {"numerator", "denominator"}:
            raise PlatformError(422, "invalid_rate")
        n, d = monetary(part["numerator"]), monetary(part["denominator"])
        if not d:
            raise PlatformError(422, "invalid_rate")
        out[kind] = {"numerator": str(n), "denominator": str(d)}
    if not any(int(out[k]["numerator"]) for k in ("input", "output")):
        raise PlatformError(422, "invalid_rate")
    return out


def cost(rate, input_tokens, output_tokens):
    integer(input_tokens)
    integer(output_tokens)
    total = sum(
        (
            Fraction(
                monetary(rate[k]["numerator"]) * n,
                integer(monetary(rate[k]["denominator"]), positive=True),
            )
            for k, n in (("input", input_tokens), ("output", output_tokens))
        ),
        Fraction(),
    )
    return integer((total.numerator + total.denominator - 1) // total.denominator)
