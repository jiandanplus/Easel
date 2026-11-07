"""Pure, bounded PG16 dump-output screening, never an SQL executor.

Policies must come from an independent trusted catalog/fixture authority, never
from the candidate archive or its extracted SQL. TOC is only the selected list;
it does not authenticate hidden archive metadata. Data results prove grammar and
coverage only: Task2B2/C must separately bind tools, files, COPY content inventory,
catalogs and stage facts before constructing any verified restore input.
"""
from collections import Counter
from dataclasses import dataclass, field
import hashlib
import re

from .contracts import B3Error


def _require(condition):
    if not condition:
        raise B3Error("invalid B3 dump screening")


def _integer(value, low, high):
    _require(type(value) is int and low <= value <= high)


def _ident(value):
    _require(type(value) is str and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", value, re.ASCII) is not None)


def _qualified(value):
    _require(type(value) is str and re.fullmatch(r"public\.[a-z_][a-z0-9_]{0,62}", value, re.ASCII) is not None)


def _hash(value):
    _require(type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value, re.ASCII) is not None)


KINDS = frozenset({"SCHEMA", "COMMENT", "ACL", "FUNCTION", "TABLE", "TABLE DATA",
    "CONSTRAINT", "FK CONSTRAINT", "INDEX", "SEQUENCE", "SEQUENCE SET",
    "SEQUENCE OWNED BY", "TRIGGER", "TYPE", "VIEW", "DEFAULT", "DEFAULT ACL",
    "RULE", "POLICY", "MATERIALIZED VIEW", "MATERIALIZED VIEW DATA"})
_KIND_PREFIXES = tuple(sorted(KINDS, key=lambda k: (-len(k), k)))


def _logical(record):
    _require(type(record) is tuple and len(record) == 4)
    kind, namespace, tag, owner = record
    _require(all(type(v) is str for v in record))
    _require(kind in KINDS)
    _require(0 < len(tag) <= 4096 and re.fullmatch(r"[!-~](?:[ -~]*[!-~])?", tag, re.ASCII) is not None)
    if namespace == "-":
        _require((kind == "SCHEMA" and tag == "public") or
                 (kind in {"COMMENT", "ACL"} and tag == "SCHEMA public"))
    else:
        _require(namespace == "public" and kind != "SCHEMA")
    if owner == "-":
        _require(kind == "COMMENT" and namespace == "public")
    else:
        _ident(owner)
        if owner == "pg_database_owner":
            _require(namespace == "-" and kind in {"SCHEMA", "COMMENT", "ACL"})


@dataclass(frozen=True, repr=False)
class TocPolicy:
    expected_records: tuple = field(repr=False)
    expected_header_entries: int | None = None

    def __post_init__(self):
        _require(type(self.expected_records) is tuple and len(self.expected_records) <= 10000)
        for record in self.expected_records:
            _logical(record)
        if self.expected_header_entries is not None:
            _integer(self.expected_header_entries, 0, 2**31-1)


@dataclass(frozen=True, repr=False)
class TocInspection:
    size: int
    sha256: str
    entry_count: int
    header_entries: int
    _records: tuple = field(repr=False)

    def __post_init__(self):
        _integer(self.size, 1, 1024**2)
        _hash(self.sha256)
        _integer(self.entry_count, 0, 10000)
        _integer(self.header_entries, 0, 2**31-1)
        _require(type(self._records) is tuple and len(self._records) == self.entry_count)
        ids = set()
        for record in self._records:
            _require(type(record) is tuple and len(record) == 7)
            dump_id, catalog_oid, object_oid = record[:3]
            _integer(dump_id, 1, 2**31-1)
            _integer(catalog_oid, 0, 2**32-1)
            _integer(object_oid, 0, 2**32-1)
            _require(dump_id not in ids)
            ids.add(dump_id)
            _logical(record[3:])


_TOC_HEADER = (
    r";", r"; Archive created at [0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} UTC",
    r";     dbname: [A-Za-z_][A-Za-z0-9_]{0,62}", r";     TOC Entries: (0|[1-9][0-9]{0,9})",
    r";     Compression: gzip", r";     Dump Version: 1\.15-0", r";     Format: CUSTOM",
    r";     Integer: 4 bytes", r";     Offset: 8 bytes",
    r";     Dumped from database version: 16\.[0-9]{1,3}(?: \([ -~]{1,200}\))?",
    r";     Dumped by pg_dump version: 16\.[0-9]{1,3}(?: \([ -~]{1,200}\))?",
    r";", r";", r"; Selected TOC Entries:", r";",
)


def parse_toc(raw: bytes, policy: TocPolicy) -> TocInspection:
    """Compare exact logical multiplicities; OIDs and ordering remain local facts."""
    _require(type(policy) is TocPolicy)
    policy.__post_init__()
    _require(type(raw) is bytes and 0 < len(raw) <= 1024**2 and b"\0" not in raw and raw.endswith(b"\n"))
    lines = raw[:-1].split(b"\n")
    _require(all(len(line) <= 8192 for line in lines))
    try:
        lines = [line.decode("utf-8", errors="strict") for line in lines]
    except UnicodeError:
        raise B3Error("invalid B3 dump screening") from None
    _require(len(lines) >= len(_TOC_HEADER))
    for line, pattern in zip(lines, _TOC_HEADER):
        _require(re.fullmatch(pattern, line, re.ASCII) is not None)
    header_entries = int(lines[3].rsplit(" ", 1)[1])
    _integer(header_entries, 0, 2**31-1)
    if policy.expected_header_entries is not None:
        _require(header_entries == policy.expected_header_entries)
    records = []
    ids = set()
    for line in lines[len(_TOC_HEADER):]:
        if line == "":
            continue
        _require(len(records) < 10000)
        match = re.fullmatch(r"([1-9][0-9]{0,9}); ([0-9]{1,10}) ([0-9]{1,10}) (.+)", line, re.ASCII)
        _require(match is not None)
        dump_id, catalog_oid, object_oid = map(int, match.group(1, 2, 3))
        _integer(dump_id, 1, 2**31-1)
        _integer(catalog_oid, 0, 2**32-1)
        _integer(object_oid, 0, 2**32-1)
        _require(dump_id not in ids)
        ids.add(dump_id)
        rest = match[4]
        kind = next((k for k in _KIND_PREFIXES if rest.startswith(k + " ")), None)
        _require(kind is not None)
        tail = rest[len(kind)+1:]
        _require(" " in tail)
        namespace, tail = tail.split(" ", 1)
        _require(" " in tail)
        tag, owner = tail.rsplit(" ", 1)
        logical = (kind, namespace, tag, owner)
        _logical(logical)
        records.append((dump_id, catalog_oid, object_oid, *logical))
    _require(Counter(r[3:] for r in records) == Counter(policy.expected_records))
    return TocInspection(len(raw), hashlib.sha256(raw).hexdigest(), len(records), header_entries, tuple(records))


@dataclass(frozen=True, repr=False)
class DataPolicy:
    """Ordered non-generated COPY columns and exact independently trusted setvals."""
    tables: tuple = field(repr=False)
    sequences: tuple = field(repr=False)

    def __post_init__(self):
        _require(type(self.tables) is tuple and len(self.tables) <= 1000)
        _require(type(self.sequences) is tuple and len(self.sequences) <= 1000)
        names = set()
        for item in self.tables:
            _require(type(item) is tuple and len(item) == 2)
            name, columns = item
            _qualified(name)
            _require(name not in names)
            names.add(name)
            _require(type(columns) is tuple and 0 < len(columns) <= 512)
            seen = set()
            for column in columns:
                _require(type(column) is str and re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", column, re.ASCII) is not None)
                _require(column not in seen)
                seen.add(column)
        sequences = set()
        for item in self.sequences:
            _require(type(item) is tuple and len(item) == 3)
            name, value, called = item
            _qualified(name)
            _integer(value, -2**63, 2**63-1)
            _require(type(called) is bool and name not in sequences and name not in names)
            sequences.add(name)


@dataclass(frozen=True, repr=False)
class DataScreen:
    size: int
    sha256: str
    table_count: int
    sequence_count: int
    row_count: int
    # (qualified table, ordered columns, row count, first row byte offset,
    #  terminator byte offset). Layout is not a COPY content digest.
    _layout: tuple = field(repr=False)

    def __post_init__(self):
        _integer(self.size, 1, 16*1024**2)
        _hash(self.sha256)
        _integer(self.table_count, 0, 1000)
        _integer(self.sequence_count, 0, 1000)
        _integer(self.row_count, 0, 100000000)
        _require(type(self._layout) is tuple and len(self._layout) == self.table_count)
        names = set()
        total = 0
        for item in self._layout:
            _require(type(item) is tuple and len(item) == 5)
            name, columns, rows, start, end = item
            DataPolicy(((name, columns),), ())
            _require(name not in names)
            names.add(name)
            _integer(rows, 0, 100000)
            _integer(start, 0, self.size)
            _integer(end, start, self.size)
            total += rows
        _require(total == self.row_count)


_DATA_HEADER = (
    b"SET statement_timeout = 0;", b"SET lock_timeout = 0;",
    b"SET idle_in_transaction_session_timeout = 0;", b"SET client_encoding = 'UTF8';",
    b"SET standard_conforming_strings = on;", b"SELECT pg_catalog.set_config('search_path', '', false);",
    b"SET check_function_bodies = false;", b"SET xmloption = content;",
    b"SET client_min_messages = warning;", b"SET row_security = off;",
)
_RESTRICT = b"\\restrict easelB2SchemaComparison20261030"
_UNRESTRICT = b"\\unrestrict easelB2SchemaComparison20261030"
_COPY = re.compile(rb"COPY (public\.[a-z_][a-z0-9_]{0,62}) \(([a-z_][a-z0-9_]{0,62}(?:, [a-z_][a-z0-9_]{0,62})*)\) FROM stdin;")
_SETVAL = re.compile(rb"SELECT pg_catalog\.setval\('(public\.[a-z_][a-z0-9_]{0,62})', (0|[1-9][0-9]{0,18}|-[1-9][0-9]{0,18}), (true|false)\);")


def screen_data_sql(raw: bytes, policy: DataPolicy) -> DataScreen:
    """Closed PG16 grammar, exact scope, raw COPY rows; never evaluate SQL/text."""
    _require(type(policy) is DataPolicy)
    policy.__post_init__()
    _require(type(raw) is bytes and 0 < len(raw) <= 16*1024**2 and b"\0" not in raw and raw.endswith(b"\n"))
    expected_tables = dict(policy.tables)
    expected_sequences = {name: (value, called) for name, value, called in policy.sequences}
    seen_tables, seen_sequences = set(), set()
    active = None
    layout = []
    phase = "before"
    header_index = 0
    offset = 0
    # Split only on LF: COPY bytes are not Unicode/SQL or splitlines tokens.
    for line in raw[:-1].split(b"\n"):
        next_offset = offset + len(line) + 1
        if active is not None:
            name, columns, rows, start = active
            if line == b"\\.":
                layout.append((name, columns, rows, start, offset))
                active = None
            else:
                _require(rows < 100000 and line.count(b"\t") == len(columns)-1)
                active = (name, columns, rows+1, start)
            offset = next_offset
            continue
        # PostgreSQL ends -- comments on CR as well as LF. An embedded CR
        # could otherwise hide an executable command from this LF scanner.
        _require(b"\r" not in line)
        _require(phase != "done" or line == b"")
        if line == b"" or line.startswith(b"--"):
            offset = next_offset
            continue
        if phase == "before":
            _require(line == _RESTRICT)
            phase = "header"
        elif phase == "header":
            _require(line == _DATA_HEADER[header_index])
            header_index += 1
            if header_index == len(_DATA_HEADER):
                phase = "copy"
        elif phase in {"copy", "sequence"}:
            if line == _UNRESTRICT:
                phase = "done"
            else:
                match = _COPY.fullmatch(line) if phase == "copy" else None
                if match is not None:
                    name = match[1].decode("ascii")
                    columns = tuple(match[2].decode("ascii").split(", "))
                    _require(name in expected_tables and name not in seen_tables and columns == expected_tables[name])
                    seen_tables.add(name)
                    active = (name, columns, 0, next_offset)
                else:
                    match = _SETVAL.fullmatch(line)
                    _require(match is not None)
                    name = match[1].decode("ascii")
                    value = int(match[2])
                    _integer(value, -2**63, 2**63-1)
                    called = match[3] == b"true"
                    _require(name in expected_sequences and name not in seen_sequences and (value, called) == expected_sequences[name])
                    seen_sequences.add(name)
                    phase = "sequence"
        else:
            _require(False)
        offset = next_offset
    _require(active is None and phase == "done")
    _require(seen_tables == set(expected_tables) and seen_sequences == set(expected_sequences))
    return DataScreen(len(raw), hashlib.sha256(raw).hexdigest(), len(layout), len(seen_sequences), sum(r[2] for r in layout), tuple(layout))


@dataclass(frozen=True, repr=False)
class SchemaHashProof:
    size: int
    sha256: str

    def __post_init__(self):
        _integer(self.size, 1, 8*1024**2)
        _hash(self.sha256)


def verify_schema_bytes(raw: bytes, expected_size: int, expected_sha256: str) -> SchemaHashProof:
    """Exact opaque byte binding only, not a DDL semantic inspection."""
    _integer(expected_size, 1, 8*1024**2)
    _hash(expected_sha256)
    _require(type(raw) is bytes and len(raw) == expected_size)
    actual = hashlib.sha256(raw).hexdigest()
    _require(actual == expected_sha256)
    return SchemaHashProof(len(raw), actual)
