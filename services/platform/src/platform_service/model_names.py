"""What a supplier's model name may look like (decision 0017, migration 035).

Some suppliers put single spaces in names ("Midjourney v8.2"). Leading, trailing or doubled
spaces, other whitespace and control characters stay refused, everywhere the name is checked.
"""
import re


def upstream_model_ok(value):
    return (isinstance(value, str) and 1 <= len(value) <= 128
            and re.fullmatch(r"\S+(?: \S+)*", value) is not None
            and not any(ord(char) < 32 or 127 <= ord(char) <= 0x9f for char in value))
