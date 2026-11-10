"""Bounded pure mountinfo model. This module never reads files or probes Linux.

IDs identify tree nodes, never ordering. Fixed-directory FD observations must
agree with the model; permission/bind policy remains the caller's validator.
"""
from __future__ import annotations

import re

ERROR_CODES = frozenset((
    "invalid_input", "size_limit", "record_limit", "malformed_record", "invalid_id",
    "duplicate_id", "invalid_path", "invalid_flags", "invalid_graph", "ambiguous_stack",
    "invalid_root", "invalid_observed", "observed_mismatch",
))
_TARGETS = ("/", "/code", "/inputs", "/work", "/sys/fs/cgroup")
_MAX_BYTES = 1024 * 1024
_MAX_RECORDS = 4096


class MountTableError(ValueError):
    """Fixed code only: no input text, path, source, ID or exception detail."""

    def __init__(self, code="invalid_input"):
        self.code = code if type(code) is str and code in ERROR_CODES else "invalid_input"
        super().__init__(self.code)


def _integer(text):
    if not re.fullmatch(r"[0-9]{1,19}", text): raise MountTableError("invalid_id")
    value = int(text)
    if not 1 <= value <= 2**63 - 1: raise MountTableError("invalid_id")
    return value


def _path(text):
    value = re.sub(r"\\(040|011|012|134)", lambda match: chr(int(match[1], 8)), text)
    if (len(value) > 4096 or not value.startswith("/") or
            re.search(r"[\x00-\x20\x7f\\]", value) or
            (value != "/" and any(part in ("", ".", "..") for part in value[1:].split("/")))):
        raise MountTableError("invalid_path")
    return value


def _beneath(path, parent):
    return parent == "/" or path == parent or path.startswith(parent + "/")


def _readonly(mount_options, super_options):
    lists = [part.split(",") for part in (mount_options, super_options)]
    if any("" in options or {"ro", "rw"} <= set(options) for options in lists):
        raise MountTableError("invalid_flags")
    if not {"ro", "rw"}.intersection(lists[0]): raise MountTableError("invalid_flags")
    return "ro" in lists[0] or "ro" in lists[1]


def resolve_mountinfo(raw: str, observed_ids: dict[str, int]) -> dict:
    """Resolve one stable snapshot, cross-checked by the five fixed FD IDs.

    A sole '/' base may parent itself or have a parent outside this table. Same-point
    siblings are unprovable forks. For a visible mount, an overmount child at
    the same point replaces it and hides its other child branches. Otherwise a
    shallower direct child covers deeper direct children on that same underlying
    mount. Descendants attached to the newly visible child remain visible.
    """
    if type(raw) is not str: raise MountTableError("invalid_input")
    if len(raw) > _MAX_BYTES: raise MountTableError("size_limit")
    try:
        if len(raw.encode("utf-8")) > _MAX_BYTES: raise MountTableError("size_limit")
    except UnicodeError:
        raise MountTableError("invalid_input") from None
    if (type(observed_ids) is not dict or len(observed_ids) != len(_TARGETS) or
            any(type(key) is not str or key not in _TARGETS for key in observed_ids) or
            any(type(value) is not int or not 1 <= value <= 2**63 - 1 for value in observed_ids.values())):
        raise MountTableError("invalid_observed")
    lines = raw.splitlines()
    if len(lines) > _MAX_RECORDS: raise MountTableError("record_limit")
    if not lines: raise MountTableError("invalid_root")
    nodes = {}
    for line in lines:
        fields = line.split()
        try:
            separator = fields.index("-")
        except ValueError:
            raise MountTableError("malformed_record") from None
        if separator < 6 or len(fields) != separator + 4:
            raise MountTableError("malformed_record")
        identity, parent = _integer(fields[0]), _integer(fields[1])
        if identity in nodes: raise MountTableError("duplicate_id")
        if not re.fullmatch(r"[0-9]{1,10}:[0-9]{1,10}", fields[2]):
            raise MountTableError("malformed_record")
        _path(fields[3])
        point = _path(fields[4])
        fs_type = fields[separator + 1]
        if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,32}", fs_type):
            raise MountTableError("malformed_record")
        nodes[identity] = {"id": identity, "parent": parent, "mount_point": point,
                           "read_only": _readonly(fields[5], fields[separator + 3]), "fs_type": fs_type}

    anchors = [node for node in nodes.values() if node["parent"] not in nodes or
               (node["parent"] == node["id"] and node["mount_point"] == "/")]
    if len(anchors) != 1 or anchors[0]["mount_point"] != "/":
        raise MountTableError("invalid_root")
    base = anchors[0]
    children = {identity: [] for identity in nodes}
    attachment_points = set()
    for identity, node in nodes.items():
        if node is base: continue
        parent = nodes[node["parent"]]
        if not _beneath(node["mount_point"], parent["mount_point"]):
            raise MountTableError("invalid_graph")
        attachment = (node["parent"], node["mount_point"])
        if attachment in attachment_points: raise MountTableError("ambiguous_stack")
        attachment_points.add(attachment)
        children[parent["id"]].append(identity)
    # Every node must belong to the one rooted tree, including hidden branches.
    # An iterative visit rejects disconnected cycles without recursion or ID order.
    pending, seen = [base["id"]], set()
    while pending:
        identity = pending.pop()
        if identity in seen: raise MountTableError("invalid_graph")
        seen.add(identity); pending.extend(children[identity])
    if len(seen) != len(nodes): raise MountTableError("invalid_graph")

    visible = {}
    pending = [base["id"]]
    while pending:
        identity = pending.pop()
        # Stack order is the parent relation, never line order or numeric IDs.
        while True:
            same = [child for child in children[identity]
                    if nodes[child]["mount_point"] == nodes[identity]["mount_point"]]
            if not same: break
            identity = same[0]  # uniqueness was proved per parent+point above
        node = nodes[identity]
        if node["mount_point"] in visible: raise MountTableError("invalid_graph")
        visible[node["mount_point"]] = node
        candidates = {nodes[child]["mount_point"]: child for child in children[identity]}
        for point, child in candidates.items():
            # Direct siblings at /a and /a/b are attached to this underlying
            # mount. /a covers that old /a/b branch; children of /a are separate.
            covered = any(other != point and _beneath(point, other) for other in candidates)
            if not covered: pending.append(child)

    result = {}
    for path in _TARGETS:
        effective = max((node for point, node in visible.items() if _beneath(path, point)),
                        key=lambda node: len(node["mount_point"]))
        if observed_ids[path] != effective["id"]: raise MountTableError("observed_mismatch")
        result[path] = {key: effective[key] for key in ("mount_point", "read_only", "fs_type")}
        result[path]["writable_descendants"] = sorted(point for point, node in visible.items()
            if point != path and _beneath(point, path) and not node["read_only"])
    return result
