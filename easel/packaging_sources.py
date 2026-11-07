"""Shared public source receipt policy; includes current untracked authored inputs.

A reviewed frozen hash map is the build authority. This collector defines candidate
public paths only; it never stages files, copies user state or follows links.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path

POLICY_PATH = Path(__file__).resolve().parents[1] / "packaging/pi/public-source-policy.json"


def source_records(root: Path, *, frontend_only=False):
    policy = json.loads(POLICY_PATH.read_text())
    records = {}
    starts = ["web/frontend"] if frontend_only else policy["roots"]
    for relative_root in starts:
        base = root / relative_root
        for current in (base, *base.parents):
            if current == root:
                break
            if current.is_symlink():
                raise ValueError("Public source root link")
        if not base.exists():
            continue
        for parent, dirs, files in os.walk(base, followlinks=False):
            directory = Path(parent)
            dirs[:] = sorted(name for name in dirs if name not in policy["excluded_directories"] and not name.startswith("."))
            for name in dirs:
                if (directory / name).is_symlink():
                    raise ValueError("Public source directory link")
            for name in sorted(files):
                path = directory / name
                relative = path.relative_to(root).as_posix()
                lower = name.lower()
                if (name.startswith(".") or lower in policy["private_names"] or any(term in lower for term in policy["private_fragments"]) or any(relative.startswith(prefix) for prefix in policy["excluded_prefixes"])):
                    continue
                frontend = relative.startswith("web/frontend/")
                extensions = policy["frontend_extensions"] if frontend else policy["extensions"]
                if path.suffix.lower() not in extensions:
                    continue
                if path.is_symlink() or not path.is_file():
                    raise ValueError("Public source must be regular, no links: " + relative)
                records[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(records.items()))


def frontend_records(root):
    return source_records(root, frontend_only=True)


def verify_source_records(root, records):
    if source_records(root) != records:
        raise ValueError("Public source authority differs or is stale; review and freeze current public sources again")
