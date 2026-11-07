"""Bounded private PG16 membership observation DATA; no read/replay authority."""
from dataclasses import dataclass, asdict
import hashlib
import json
from .reconstruction_context import (SourceSample, _Redacted, _require, _name,
                                     _integer, _xmin, _budget)

MAX_EDGES = 16384
MAX_BYTES = 8 * 1024 * 1024


def _pin(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True, repr=False)
class GrantGraphScope(_Redacted):
    kind: str
    role_names: tuple

    def __post_init__(self):
        _require(type(self.kind) is str and self.kind in ('central', 'identity'))
        _require(type(self.role_names) is tuple and 0 < len(self.role_names) <= 1024)
        for name in self.role_names:
            _name(name)
        _require(len(set(self.role_names)) == len(self.role_names))


@dataclass(frozen=True, repr=False)
class GrantEdge(_Redacted):
    role_oid: int
    role_name: str
    member_oid: int
    member_name: str
    grantor_oid: int
    grantor_name: str
    admin_option: bool
    inherit_option: bool
    set_option: bool
    xmin: str

    def __post_init__(self):
        for value in (self.role_oid, self.member_oid, self.grantor_oid):
            _integer(value)
        for value in (self.role_name, self.member_name, self.grantor_name):
            _name(value)
        for value in (self.admin_option, self.inherit_option, self.set_option):
            _require(type(value) is bool)
        _xmin(self.xmin)

    def membership(self):
        return (self.role_name, self.member_name, self.admin_option,
                self.grantor_name, self.inherit_option, self.set_option)


@dataclass(frozen=True, repr=False)
class GrantGraphSample(_Redacted):
    scope: GrantGraphScope
    source_sample: SourceSample
    edges: tuple

    def __post_init__(self):
        _require(type(self.scope) is GrantGraphScope and type(self.source_sample) is SourceSample)
        _require(self.scope.kind == self.source_sample.scope.kind)
        _require(set(self.scope.role_names) <= set(self.source_sample.scope.selected_role_names))
        _require(type(self.edges) is tuple and len(self.edges) <= MAX_EDGES)
        names = {r.name: r.oid for r in self.source_sample.roles}
        oids = {r.oid: r.name for r in self.source_sample.roles}
        keys = set()
        for edge in self.edges:
            _require(type(edge) is GrantEdge)
            _require(edge.role_name in self.scope.role_names or edge.member_name in self.scope.role_names)
            key = (edge.role_name, edge.member_name, edge.grantor_name)
            _require(key not in keys)
            keys.add(key)
            for name, oid in ((edge.role_name, edge.role_oid), (edge.member_name, edge.member_oid),
                              (edge.grantor_name, edge.grantor_oid)):
                _require(names.get(name, oid) == oid and oids.get(oid, name) == name)
                names[name], oids[oid] = oid, name
        encoded = json.dumps([asdict(e) for e in self.edges], ensure_ascii=False).encode()
        _require(_budget(self.source_sample) + len(encoded) <= MAX_BYTES)

    @property
    def kind(self):
        return self.scope.kind

    @property
    def phase(self):
        return self.source_sample.phase

    @property
    def source_sample_pin(self):
        return self.source_sample.safe_summary()['full_sha256']

    @property
    def source_sample_full_pin(self):
        return self.source_sample_pin

    @property
    def source_identity_pin(self):
        value = asdict(self.source_sample.identity)
        del value['backend_pid'], value['snapshot_text']
        return _pin(value)

    @property
    def authorized_read_names(self):
        return self.scope.role_names

    def stable_private_value(self):
        return {'format': 'b3-source-grant-graph-data-v1', 'kind': self.kind,
                'source_identity_pin': self.source_identity_pin,
                'source_read_scope': asdict(self.source_sample.scope),
                'authorized_read_names': self.scope.role_names,
                'edges': tuple(tuple(asdict(e).values()) for e in self.edges)}

    def safe_summary(self):
        return {'format': 'b3-source-grant-graph-data-v1', 'edge_count': len(self.edges),
                'source_sample_pin': self.source_sample_pin,
                'stable_sha256': _pin(self.stable_private_value()),
                'data_only': True, 'native_verified': False}


def parse_grant_graph(scope, sample, rows):
    _require(type(rows) in (list, tuple) and len(rows) <= MAX_EDGES)
    edges = []
    used = _budget(sample)
    for row in rows:
        _require(type(row) in (list, tuple) and len(row) == 10)
        edge = GrantEdge(*row)
        used += len(json.dumps(asdict(edge), ensure_ascii=False).encode())
        _require(used <= MAX_BYTES)
        edges.append(edge)
    edges.sort(key=lambda e: (e.role_name, e.member_name, e.grantor_name))
    return GrantGraphSample(scope, sample, tuple(edges))
