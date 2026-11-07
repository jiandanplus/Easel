"""Public metadata for preverified provider bindings, never credential storage."""
import json
import os
import re


def provider_registry(env=None):
    env = os.environ if env is None else env
    try:
        raw = env.get("CENTRAL_CONSOLE_PROVIDER_REGISTRY", "[]")
        if not isinstance(raw, str) or len(raw) > 32768:
            raise ValueError()
        entries = json.loads(raw)
        if not isinstance(entries, list) or len(entries) > 32:
            raise ValueError()
        identities = set()
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {"id", "version", "models"}:
                raise ValueError()
            for field in ("id", "version"):
                if not isinstance(entry[field], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", entry[field]):
                    raise ValueError()
            identity = (entry["id"], entry["version"])
            if identity in identities:
                raise ValueError()
            identities.add(identity)
            models = entry["models"]
            if not isinstance(models, dict) or len(models) > 100:
                raise ValueError()
            for model, binding in models.items():
                if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", model):
                    raise ValueError()
                if not isinstance(binding, dict) or set(binding) != {"upstream_model", "verification", "verification_ref"}:
                    raise ValueError()
                if binding["verification"] not in {"live", "fixture"}:
                    raise ValueError()
                for field in ("upstream_model", "verification_ref"):
                    value = binding[field]
                    if not isinstance(value, str) or not 1 <= len(value) <= 255 or any(ord(char) < 32 for char in value):
                        raise ValueError()
        return tuple(entries)
    except (ValueError, TypeError, KeyError):
        raise ValueError("invalid_console_provider_registry") from None
