"""Closed private replay DATA and deterministic PG16 grant-prefix planning.

Nothing in this module grants authority or performs IO. Native qualification is
separate. Owner admission must bind the exact original policy and observations.
"""
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
import hashlib
import json
import math
from ..media_backup_b2.reconstruction_context import (SourceSample, SourcePhase,
    RoleState, DatabaseState, _Redacted, _name, _config, _text, _require)
from ..media_backup_b2.reconstruction_grants import GrantGraphScope, GrantGraphSample
from .contracts import ComparisonContext, VERIFIER
from .target_contract import TargetSpec
from ..media_backup_b2.contracts import B2Error

FORMAT = 'b3-role-replay-authorization-v1'
MAX_EDGES = 16384
MAX_AUTHORIZATION_BYTES = 8 * 1024 * 1024
MAX_PREFIX_WORK = 4 * 1024 * 1024
# Root verified REL_16_15 pg_wchar.h / encnames.c canonical backend encodings.
PG16_DATABASE_ENCODINGS = (
    'SQL_ASCII','EUC_JP','EUC_CN','EUC_KR','EUC_TW','EUC_JIS_2004','UTF8',
    'MULE_INTERNAL','LATIN1','LATIN2','LATIN3','LATIN4','LATIN5','LATIN6','LATIN7',
    'LATIN8','LATIN9','LATIN10','WIN1256','WIN1258','WIN866','WIN874','KOI8R',
    'WIN1251','WIN1252','ISO_8859_5','ISO_8859_6','ISO_8859_7','ISO_8859_8',
    'WIN1250','WIN1253','WIN1254','WIN1255','WIN1257','KOI8U')


class RolePlanningError(B2Error):
    """Safe closed diagnostic; no observed values in public exceptions."""
    def __init__(self, code):
        _require(code in ('UNPROVABLE_PREFIX', 'BOUNDED_EXHAUSTION'))
        super().__init__()
        self.code = code


def _work(budget, count=1):
    if budget is not None:
        budget[0] -= count
        if budget[0] < 0:
            raise RolePlanningError('BOUNDED_EXHAUSTION')


def _canonical_chunks(value, *, compact=True):
    """Walk records directly; never expand a repeated child into a full tree.

    String fragments are at most 1024 input characters (JSON escaping can grow
    them sixfold). Fragmenting inside the quotes preserves JSON's exact scalar
    encoding, including control characters and non-ASCII UTF-8.
    """
    if isinstance(value, Enum):
        yield from _canonical_chunks(value.value, compact=compact)
    elif is_dataclass(value) and not isinstance(value, type):
        yield b'{'
        for index, field in enumerate(sorted(fields(value), key=lambda f: f.name)):
            if index:
                yield b',' if compact else b', '
            yield from _canonical_chunks(field.name, compact=compact)
            yield b':' if compact else b': '
            yield from _canonical_chunks(getattr(value, field.name), compact=compact)
        yield b'}'
    elif type(value) is dict:
        _require(all(type(key) is str for key in value))
        yield b'{'
        for index, key in enumerate(sorted(value)):
            if index:
                yield b',' if compact else b', '
            yield from _canonical_chunks(key, compact=compact)
            yield b':' if compact else b': '
            yield from _canonical_chunks(value[key], compact=compact)
        yield b'}'
    elif type(value) in (list, tuple):
        yield b'['
        for index, item in enumerate(value):
            if index:
                yield b',' if compact else b', '
            yield from _canonical_chunks(item, compact=compact)
        yield b']'
    elif type(value) is str:
        yield b'"'
        for offset in range(0, len(value), 1024):
            try:
                yield json.dumps(value[offset:offset+1024], ensure_ascii=False)[1:-1].encode('utf-8')
            except (UnicodeError, ValueError):
                raise B2Error() from None
        yield b'"'
    else:
        _require(value is None or type(value) in (bool, int, float))
        _require(type(value) is not float or math.isfinite(value))
        yield json.dumps(value, ensure_ascii=False).encode('utf-8')


def _authorization_budget(value):
    # Preserve the existing authorization cap's default JSON whitespace count.
    # Hashes below use the unchanged sorted/compact canonical representation.
    remaining = MAX_AUTHORIZATION_BYTES
    for chunk in _canonical_chunks(value, compact=False):
        remaining -= len(chunk)
        _require(remaining >= 0)


def _hash(value):
    digest = hashlib.sha256()
    for chunk in _canonical_chunks(value):
        digest.update(chunk)
    return digest.hexdigest()


def _value(value):
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, '__dataclass_fields__'):
        return {f.name: _value(getattr(value, f.name)) for f in fields(value)}
    if type(value) in (list, tuple):
        return [_value(v) for v in value]
    if type(value) is dict:
        return {k: _value(v) for k, v in value.items()}
    return value


def _digest(value):
    _require(type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value))


def _tuple(value, maximum):
    _require(type(value) is tuple and len(value) <= maximum)


def _unique(value):
    _require(len(set(value)) == len(value))


class RolePurpose(str, Enum):
    RESTORED = 'RESTORED'
    ORDINARY_GRANTOR = 'ORDINARY_GRANTOR'
    BOOTSTRAP_HANDOVER = 'BOOTSTRAP_HANDOVER'


class SettingScope(str, Enum):
    GLOBAL = 'GLOBAL'
    ROLE = 'ROLE'
    DATABASE = 'DATABASE'
    DATABASE_ROLE = 'DATABASE_ROLE'


class AuthenticationMode(str, Enum):
    POSITIVE_SCRAM = 'POSITIVE_SCRAM'
    DENY_WRONG_PASSWORD = 'DENY_WRONG_PASSWORD'
    DENY_NULL = 'DENY_NULL'
    DENY_NOLOGIN = 'DENY_NOLOGIN'


@dataclass(frozen=True, repr=False)
class ScopedSetting(_Redacted):
    scope: SettingScope
    database_name: str | None
    role_name: str | None
    config: tuple | None

    def __post_init__(self):
        _require(type(self.scope) is SettingScope)
        for name in (self.database_name, self.role_name):
            if name is not None:
                _name(name)
        _require((self.database_name is not None) == (self.scope in (SettingScope.DATABASE, SettingScope.DATABASE_ROLE)))
        _require((self.role_name is not None) == (self.scope in (SettingScope.ROLE, SettingScope.DATABASE_ROLE)))
        _config(self.config)


@dataclass(frozen=True, repr=False)
class RoleReplayExpectation(_Redacted):
    name: str
    superuser: bool
    inherit: bool
    createrole: bool
    createdb: bool
    login: bool
    replication: bool
    connlimit: int
    validuntil: str | None
    bypassrls: bool
    config: tuple | None
    verifier: str | None
    purpose: RolePurpose
    expected_source_oid: int | None = None

    def __post_init__(self):
        RoleState(10, self.name, self.superuser, self.inherit, self.createrole,
                  self.createdb, self.login, self.replication, self.connlimit,
                  self.validuntil, self.bypassrls, self.config, self.verifier, '0')
        _require(self.verifier is None or VERIFIER.fullmatch(self.verifier) is not None)
        _require(type(self.purpose) is RolePurpose)
        if self.purpose is RolePurpose.BOOTSTRAP_HANDOVER:
            _require(type(self.expected_source_oid) is int and self.expected_source_oid == 10)
        else:
            _require(self.expected_source_oid is None)

    def attributes(self):
        return tuple(getattr(self, f) for f in ('name','superuser','inherit','createrole',
            'createdb','login','replication','connlimit','validuntil','bypassrls','config'))


@dataclass(frozen=True, repr=False)
class DatabaseReplayExpectation(_Redacted):
    name: str
    owner_name: str
    encoding_id: int
    encoding_name: str
    collate: str
    ctype: str
    provider: str
    icu_locale: str | None
    icu_rules: str | None
    recorded_version: str | None
    actual_version: str | None
    acl: str | None
    connlimit: int
    istemplate: bool
    allowconn: bool
    tablespace_name: str
    settings: tuple

    def __post_init__(self):
        DatabaseState(1, self.name, 1, self.owner_name, *tuple(getattr(self, f) for f in
            ('encoding_id','encoding_name','collate','ctype','provider','icu_locale',
             'icu_rules','recorded_version','actual_version','acl','connlimit','istemplate',
             'allowconn','tablespace_name')), '0')
        _require(self.encoding_id < len(PG16_DATABASE_ENCODINGS) and PG16_DATABASE_ENCODINGS[self.encoding_id] == self.encoding_name)
        _tuple(self.settings, 4096)
        _require(all(type(s) is ScopedSetting for s in self.settings))
        _unique(tuple((s.scope,s.database_name,s.role_name) for s in self.settings))


@dataclass(frozen=True, repr=False)
class AuthenticationRequirement(_Redacted):
    role_name: str
    mode: AuthenticationMode
    credential_id: str
    credential_sha256: str

    def __post_init__(self):
        _name(self.role_name)
        _require(type(self.mode) is AuthenticationMode)
        _name(self.credential_id)
        _digest(self.credential_sha256)


@dataclass(frozen=True, repr=False)
class ReplaySelection(_Redacted):
    format: str
    kind: str
    business_role_names: tuple
    replay_role_names: tuple
    allowed_grantors: tuple
    policy_record_sha256: str
    source_container_id: str
    system_identifier_sha256: str
    database_name: str

    def __post_init__(self):
        _require(self.format == 'b3-replay-selection-v1' and type(self.format) is str)
        _require(type(self.kind) is str and self.kind in ('central','identity'))
        for names in (self.business_role_names,self.replay_role_names,self.allowed_grantors):
            _tuple(names, 1024)
            _unique(names)
            for name in names:
                _name(name)
        _require(bool(self.business_role_names) and set(self.business_role_names) <= set(self.replay_role_names))
        _require(not set(self.replay_role_names) & set(self.allowed_grantors))
        for value in (self.policy_record_sha256,self.source_container_id,self.system_identifier_sha256):
            _digest(value)
        _name(self.database_name)


@dataclass(frozen=True, repr=False)
class ReplayAuthorizationData(_Redacted):
    format: str
    kind: str
    restore_id: str
    started_at: float
    deadline: float
    source_container_id: str
    system_identifier_sha256: str
    database_name: str
    policy_record_sha256: str
    roles: tuple
    database: DatabaseReplayExpectation
    memberships: tuple
    database_acls: tuple
    allowed_builtin_references: tuple
    authentication: tuple
    grant_graph_scope: GrantGraphScope | None = None

    def __post_init__(self):
        _require(type(self.format) is str and self.format == FORMAT)
        _require(type(self.kind) is str and self.kind in ('central','identity'))
        _text(self.restore_id, 256, empty=False)
        _require(type(self.started_at) is float and type(self.deadline) is float and
                 math.isfinite(self.started_at) and math.isfinite(self.deadline) and 0 <= self.started_at < self.deadline)
        for value in (self.source_container_id,self.system_identifier_sha256,self.policy_record_sha256):
            _digest(value)
        _name(self.database_name)
        _tuple(self.roles, 1024)
        _require(bool(self.roles) and all(type(r) is RoleReplayExpectation for r in self.roles))
        _unique(tuple(r.name for r in self.roles))
        _require(sum(r.purpose is RolePurpose.BOOTSTRAP_HANDOVER for r in self.roles) <= 1)
        _require(type(self.database) is DatabaseReplayExpectation and self.database.name == self.database_name)
        _tuple(self.memberships, MAX_EDGES)
        for row in self.memberships:
            _membership(row)
        _unique(tuple((r[0],r[1],r[3]) for r in self.memberships))
        _tuple(self.database_acls, MAX_EDGES)
        for row in self.database_acls:
            _acl(row, self.database_name)
        _unique(self.database_acls)
        _tuple(self.allowed_builtin_references, 32)
        _unique(self.allowed_builtin_references)
        _require(all(n in ('PUBLIC','pg_database_owner') for n in self.allowed_builtin_references))
        _tuple(self.authentication, 2048)
        _require(all(type(a) is AuthenticationRequirement for a in self.authentication))
        _unique(tuple((a.role_name,a.mode) for a in self.authentication))
        _unique(tuple(a.credential_id for a in self.authentication))
        _require(all(a.role_name in self.replay_role_names for a in self.authentication))
        _require(self.grant_graph_scope is None or (type(self.grant_graph_scope) is GrantGraphScope and self.grant_graph_scope.kind == self.kind))
        _authorization_budget(self)

    @property
    def replay_role_names(self):
        return tuple(r.name for r in self.roles)

    def safe_summary(self):
        return {'format': self.format, 'sha256': _hash(self), 'role_count': len(self.roles),
                'membership_count': len(self.memberships), 'data_only': True,
                'native_verified': False, 'SQL_restore': False}


def _membership(row):
    _require(type(row) is tuple and len(row) == 6)
    for i in (0,1,3):
        _name(row[i])
    _require(all(type(row[i]) is bool for i in (2,4,5)))


def _acl(row, database_name):
    _require(type(row) is tuple and len(row) == 7)
    _require(row[:3] == ('', database_name, 'database'))
    for value in row[3:5]:
        _require(value == 'PUBLIC' or (type(value) is str and value != ''))
        if value != 'PUBLIC':
            _name(value)
    _require(type(row[5]) is str and row[5] in ('CONNECT','CREATE','TEMPORARY'))
    _require(type(row[6]) is bool)


@dataclass(frozen=True, repr=False)
class RoleDefinition(_Redacted):
    expectation: RoleReplayExpectation
    initial_login: bool = False

    def __post_init__(self):
        _require(type(self.expectation) is RoleReplayExpectation and self.expectation.purpose is not RolePurpose.BOOTSTRAP_HANDOVER)
        _require(type(self.initial_login) is bool and self.initial_login is False)


@dataclass(frozen=True, repr=False)
class MembershipGrant(_Redacted):
    role_name: str
    member_name: str
    admin_option: bool
    grantor_name: str
    inherit_option: bool
    set_option: bool
    witness: tuple

    def __post_init__(self):
        _membership((self.role_name,self.member_name,self.admin_option,self.grantor_name,self.inherit_option,self.set_option))
        _tuple(self.witness, MAX_EDGES)
        _require(bool(self.witness))
        if self.witness != ('BOOTSTRAP_OID10',):
            for edge in self.witness:
                _membership(edge)
                _require(edge[0] == self.role_name and edge[1] == self.grantor_name and edge[2])


@dataclass(frozen=True, repr=False)
class DatabaseDefinition(_Redacted):
    name: str
    owner_name: str
    encoding_id: int
    encoding_name: str
    collate: str
    ctype: str
    provider: str
    icu_locale: str | None
    icu_rules: str | None
    recorded_version: str | None
    actual_version: str | None
    connlimit: int
    istemplate: bool
    allowconn: bool
    tablespace_name: str
    template_name: str = 'template0'

    def __post_init__(self):
        _require(type(self.template_name) is str and self.template_name == 'template0')
        DatabaseState(1,self.name,1,self.owner_name,self.encoding_id,self.encoding_name,
            self.collate,self.ctype,self.provider,self.icu_locale,self.icu_rules,
            self.recorded_version,self.actual_version,None,self.connlimit,self.istemplate,
            self.allowconn,self.tablespace_name,'0')


@dataclass(frozen=True, repr=False)
class DatabaseAclGrant(_Redacted):
    object_schema: str
    object_name: str
    object_type: str
    grantor_name: str
    grantee_name: str
    privilege: str
    grantable: bool

    def __post_init__(self):
        _acl(tuple(getattr(self,f.name) for f in fields(self)),self.object_name)


@dataclass(frozen=True, repr=False)
class BootstrapHandoverRequirement(_Redacted):
    expectation: RoleReplayExpectation
    source_oid: int

    def __post_init__(self):
        _require(type(self.expectation) is RoleReplayExpectation and self.expectation.purpose is RolePurpose.BOOTSTRAP_HANDOVER)
        _require(type(self.source_oid) is int and self.source_oid == 10)


@dataclass(frozen=True, repr=False)
class RoleDatabasePlan(_Redacted):
    kind: str
    roles: tuple
    memberships: tuple
    database: DatabaseDefinition
    settings: tuple
    database_acls: tuple
    handover: BootstrapHandoverRequirement | None
    authentication: tuple
    source_provenance: tuple
    source_projection_sha256: str
    authorization_sha256: str
    source_roles: tuple
    source_database: DatabaseState
    source_settings: tuple
    source_memberships: tuple
    source_graph_edges: tuple

    def __post_init__(self):
        _require(type(self.kind) is str and self.kind in ('central','identity'))
        for items,cls,maximum in ((self.roles,RoleDefinition,1024),
                (self.memberships,MembershipGrant,MAX_EDGES),
                (self.settings,ScopedSetting,4096),(self.database_acls,DatabaseAclGrant,MAX_EDGES),
                (self.authentication,AuthenticationRequirement,2048),
                (self.source_roles,RoleState,1024)):
            _tuple(items,maximum)
            _require(all(type(item) is cls for item in items))
        _require(type(self.database) is DatabaseDefinition and type(self.source_database) is DatabaseState)
        _require(self.handover is None or type(self.handover) is BootstrapHandoverRequirement)
        _digest(self.source_projection_sha256)
        _digest(self.authorization_sha256)
        _tuple(self.source_provenance,32)
        for item in self.source_provenance:
            _require(type(item) is tuple and len(item) == 2)
            _name(item[0])
            _digest(item[1])
        _tuple(self.source_memberships,MAX_EDGES)
        _tuple(self.source_settings,4096)
        _tuple(self.source_graph_edges,MAX_EDGES)

    def safe_summary(self):
        return {'format':'b3-role-database-plan-v1','sha256':_hash(self),
                'role_count':len(self.roles),'membership_count':len(self.memberships),
                'data_only':True,'native_verified':False,'SQL_restore':False}


def _cycle(edges):
    successors = {}
    for role,member,*unused in edges:
        successors.setdefault(member,set()).add(role)
    # Iterative DFS: roles can reach 1024; do not rely on Python recursion limit.
    color = {}
    for start in sorted(successors):
        if color.get(start):
            continue
        color[start] = 1
        stack = [(start, iter(sorted(successors.get(start,()))))]
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                color[node] = 2
                stack.pop()
            else:
                _require(color.get(child) != 1)
                if color.get(child,0) == 0:
                    color[child] = 1
                    stack.append((child,iter(sorted(successors.get(child,())))))


def _admin(prefix, role, member):
    return any(e[0] == role and e[1] == member and e[2] for e in prefix)


def _circular_admin(prefix, edge, bootstrap, budget=None):
    """REL16_15 user.c plan_member_revoke/plan_recursive_revoke fixed point."""
    role, recipient, admin, grantor, unused_inherit, unused_set = edge
    if not admin or grantor == bootstrap:
        return False
    if recipient == bootstrap:
        return True
    _work(budget, len(prefix))
    rows = [e for e in prefix if e[0] == role]
    keep = [True] * len(rows)
    admin_count, outgoing = {}, {}
    for i,e in enumerate(rows):
        if e[2]:
            admin_count[e[1]] = admin_count.get(e[1],0) + 1
        outgoing.setdefault(e[3],[]).append(i)
    pending = [i for i,e in enumerate(rows) if e[1] == recipient]
    while pending:
        _work(budget)
        i = pending.pop()
        if not keep[i]:
            continue
        keep[i] = False
        removed = rows[i]
        if not removed[2]:
            continue
        admin_count[removed[1]] -= 1
        if admin_count[removed[1]] == 0:
            pending.extend(outgoing.get(removed[1],()))
    return not any(keep[i] and e[1] == grantor and e[2] for i,e in enumerate(rows))


def plan_memberships(memberships, *, bootstrap_name=None):
    """Deterministic bounded admissible-prefix order; no invented support edges."""
    _tuple(memberships, MAX_EDGES)
    for edge in memberships:
        _membership(edge)
    _unique(tuple((e[0],e[1],e[3]) for e in memberships))
    _cycle(memberships)
    pending = sorted(memberships)
    prefix, result = [], []
    budget = [MAX_PREFIX_WORK]
    admin_members = set()
    while pending:
        progress = False
        for edge in tuple(pending):
            _work(budget)
            role,member,admin,grantor,inherit,set_option = edge
            supported = grantor == bootstrap_name or (grantor != role and (role,grantor) in admin_members)
            if not supported or _circular_admin(prefix,edge,bootstrap_name,budget):
                continue
            witness = ('BOOTSTRAP_OID10',) if grantor == bootstrap_name else tuple(
                e for e in prefix if e[0] == role and e[1] == grantor and e[2])
            result.append(MembershipGrant(*edge,witness))
            prefix.append(edge)
            if admin:
                admin_members.add((role,member))
            pending.remove(edge)
            progress = True
        if not progress:
            raise RolePlanningError('UNPROVABLE_PREFIX')
    _require(set(prefix) == set(memberships))
    return tuple(result)


def _rows(observation, section, name):
    _require(type(observation) is dict and type(observation.get(section)) is dict)
    rows = observation[section].get(name)
    _require(type(rows) in (tuple,list) and len(rows) <= 100000)
    _require(all(type(r) in (tuple,list) for r in rows))
    return tuple(tuple(tuple(v) if type(v) is list else v for v in r) for r in rows)


def _settings(sample):
    result = []
    for setting in sample.settings:
        scope = (SettingScope.DATABASE_ROLE if setting.role_oid else SettingScope.DATABASE) if setting.database_oid else (SettingScope.ROLE if setting.role_oid else SettingScope.GLOBAL)
        result.append(ScopedSetting(scope,sample.database.name if setting.database_oid else None,
                                    setting.role_name,setting.config))
    return tuple(sorted(result,key=lambda s:(s.scope.value,s.database_name or '',s.role_name or '')))


def build_role_database_plan(*, sample, observation, context, authorization,
                             target_spec, provenance, grant_sample=None):
    _require(type(context) is ComparisonContext and type(authorization) is ReplayAuthorizationData)
    # Full phase tuple is mandatory: one DURING observation alone proves no stability.
    _require(type(sample) is tuple and len(sample) == 3 and all(type(s) is SourceSample for s in sample))
    _require(tuple(s.phase for s in sample) == (SourcePhase.BEFORE,SourcePhase.DURING,SourcePhase.AFTER))
    _require(all(s.scope is sample[0].scope for s in sample))
    _require(all(s.stable_private_value() == sample[0].stable_private_value() for s in sample))
    phases = sample
    sample = sample[1]
    a = authorization
    _require(a.kind == context.source_kind == sample.scope.kind)
    _require(a.source_container_id == sample.scope.container_id and a.system_identifier_sha256 == sample.scope.system_identifier_sha256)
    _require(a.database_name == context.database_name == sample.database.name)
    names = {r.name:r for r in sample.roles}
    expected = {r.name:r for r in a.roles}
    _require(set(context.role_names) <= set(expected))
    _require(set(expected) <= set(context.role_names) | set(context.allowed_grantors))
    _require(set(expected) <= set(sample.scope.selected_role_names))
    for name,e in expected.items():
        r = names.get(name)
        _require(r is not None and e.attributes() == tuple(getattr(r,f) for f in ('name','superuser','inherit','createrole','createdb','login','replication','connlimit','validuntil','bypassrls','config')) and e.verifier == r.verifier)
        if e.purpose is RolePurpose.BOOTSTRAP_HANDOVER:
            _require(r.oid == e.expected_source_oid == 10)
        else:
            _require(r.oid != 10)
    role_rows = _rows(observation,'roles','roles')
    credential_rows = _rows(observation,'roles','credentials')
    _require(all(len(r) == 11 for r in role_rows) and all(len(r) == 3 for r in credential_rows))
    _require(len(role_rows) == len(context.role_names) and {r[0] for r in role_rows} == set(context.role_names))
    _require(len(credential_rows) == len(context.role_names) and {r[0] for r in credential_rows} == set(context.role_names))
    for row in role_rows:
        _require(_hash(row) == _hash(expected[row[0]].attributes()))
    for row in credential_rows:
        _require(row == (row[0],names[row[0]].verifier,names[row[0]].auth_xmin))
    d = a.database
    _require(all(getattr(d,f.name) == getattr(sample.database,f.name) for f in fields(d) if f.name != 'settings'))
    _require(d.owner_name in expected or d.owner_name in a.allowed_builtin_references)
    actual_settings = _settings(sample)
    _require(set(actual_settings) == set(d.settings) and len(actual_settings) == len(d.settings))
    for setting in actual_settings:
        _require(setting.role_name is None or setting.role_name in expected or setting.role_name in a.allowed_builtin_references)
        for config in setting.config or ():
            _require(config.partition('=')[0] in context.allowed_guc_keys)
    dbrows = _rows(observation,'schema','database')
    _require(_hash(dbrows) == _hash(((d.name,d.owner_name,d.acl,d.connlimit,d.istemplate,d.allowconn),)))
    memberships = _rows(observation,'roles','memberships')
    _require(all(len(r) == 7 for r in memberships))
    for row in memberships:
        _membership(row[:6])
        _require(type(row[6]) is str and row[6].isascii() and row[6].isdigit() and len(row[6]) <= 10 and int(row[6]) <= 2**32-1)
    _require(len(memberships) == len(a.memberships) and {r[:6] for r in memberships} == set(a.memberships))
    _require(all(e[0] in context.role_names and e[1] in context.role_names and e[3] in expected for e in a.memberships))
    if a.grant_graph_scope is not None:
        _require(a.grant_graph_scope.role_names == context.role_names)
        _require(type(grant_sample) is tuple and len(grant_sample) == 3)
        for index,g in enumerate(grant_sample):
            _require(type(g) is GrantGraphSample and g.scope is a.grant_graph_scope and g.source_sample is phases[index])
        _require(all(g.stable_private_value() == grant_sample[0].stable_private_value() for g in grant_sample))
        graph = grant_sample[1]
        graph_rows = {e.membership()+(e.xmin,) for e in graph.edges if e.role_name in context.role_names and e.member_name in context.role_names}
        _require(graph_rows == set(memberships))
        # Incident endpoint closure must already have been declared for strict export.
        _require(all(e.role_name in context.role_names and e.member_name in context.role_names for e in graph.edges))
    else:
        _require(grant_sample is None)
    handovers = [e for e in a.roles if e.purpose is RolePurpose.BOOTSTRAP_HANDOVER]
    bootstrap = handovers[0].name if handovers else None
    used_names = (set(context.role_names) | {d.owner_name}
                  | {e[i] for e in a.memberships for i in (0,1,3)}
                  | {n for row in a.database_acls for n in row[3:5]}
                  | {s.role_name for s in actual_settings if s.role_name is not None})
    _require(all(name in used_names for name in set(expected) - set(context.role_names)))
    if bootstrap is not None:
        _require(bootstrap in used_names)
    grants = plan_memberships(a.memberships,bootstrap_name=bootstrap)
    acls = tuple(r for r in _rows(observation,'roles','acls') if len(r) == 7 and r[2] == 'database')
    _require(len(acls) == len(a.database_acls) and set(acls) == set(a.database_acls))
    for row in acls:
        _acl(row,d.name)
        _require(all(n in expected or n in a.allowed_builtin_references for n in row[3:5]))
    for e in a.roles:
        modes = {r.mode for r in a.authentication if r.role_name == e.name}
        required = ({AuthenticationMode.DENY_NOLOGIN} if not e.login else
                    {AuthenticationMode.DENY_NULL} if e.verifier is None else
                    {AuthenticationMode.POSITIVE_SCRAM,AuthenticationMode.DENY_WRONG_PASSWORD})
        _require(modes == required)
        if e.login and e.verifier is not None:
            _require(e.verifier.startswith('SCRAM-SHA-256$'))
    _require(type(provenance) in (dict,tuple))
    provenance = tuple(sorted(provenance.items() if type(provenance) is dict else provenance))
    _unique(tuple(k for k,v in provenance))
    for key,value in provenance:
        _name(key)
        _digest(value)
    # TargetSpec is pure but belongs to a separate capability contract; compare
    # its exposed selection, never call target methods or inspect an authority.
    _require(type(target_spec) is TargetSpec and target_spec.kind == a.kind)
    _require(target_spec.restore_id == a.restore_id and target_spec.started_at == a.started_at and target_spec.deadline == a.deadline)
    _require(target_spec.source_container_id == a.source_container_id and target_spec.source_system_id_sha256 == a.system_identifier_sha256)
    _require(target_spec.bootstrap_role not in set(expected) | set(context.allowed_grantors) | set(target_spec.restored_role_names))
    target_roles = getattr(target_spec,'restored_role_names',None)
    if target_roles is not None:
        _require(set(context.role_names) <= set(target_roles))
    return RoleDatabasePlan(a.kind,tuple(RoleDefinition(e) for e in sorted(a.roles,key=lambda e:e.name) if e.purpose is not RolePurpose.BOOTSTRAP_HANDOVER),
        grants,DatabaseDefinition(*tuple(getattr(d,f.name) for f in fields(DatabaseDefinition) if f.name != 'template_name')),actual_settings,tuple(DatabaseAclGrant(*r) for r in sorted(acls)),
        BootstrapHandoverRequirement(handovers[0],10) if handovers else None,
        a.authentication,provenance,_hash(tuple(s.to_private_value() for s in phases)),_hash(a),
        sample.roles,sample.database,sample.settings,memberships,grant_sample[1].edges if grant_sample is not None else ())


def build_role_database_pair(*, samples, observations, contexts, authorizations,
                             target_specs, provenance, grant_samples=None):
    values = (samples,observations,contexts,authorizations,target_specs,provenance)
    _require(all(type(v) is tuple and len(v) == 2 for v in values))
    _require(tuple(a.kind for a in authorizations) == ('central','identity'))
    _require(authorizations[0].restore_id == authorizations[1].restore_id)
    union = tuple(sorted(set(contexts[0].role_names) | set(contexts[1].role_names)))
    _require(all(type(t) is TargetSpec and tuple(sorted(t.restored_role_names)) == union for t in target_specs))
    if grant_samples is None:
        grant_samples = (None,None)
    _require(type(grant_samples) is tuple and len(grant_samples) == 2)
    return tuple(build_role_database_plan(sample=samples[i],observation=observations[i],
        context=contexts[i],authorization=authorizations[i],target_spec=target_specs[i],
        provenance=provenance[i],grant_sample=grant_samples[i]) for i in range(2))
