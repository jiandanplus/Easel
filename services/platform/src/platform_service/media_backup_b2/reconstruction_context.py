"""Private PG16 observation DATA. No ownership, admission or execution authority."""
import base64
import binascii
from dataclasses import dataclass, fields
from enum import Enum
import hashlib
import json
import re

from .contracts import B2Error

_MAX_BYTES = 8 * 1024 * 1024
_U32 = 2**32 - 1
_U64 = 2**64 - 1
_I32 = 2**31 - 1
_FORMAT = "b3-source-context-data-v1"


def _require(condition):
    if not condition:
        raise B2Error()


def _text(value, maximum=1024, *, empty=True):
    _require(type(value) is str and (empty or bool(value)))
    _require(len(value) <= maximum and "\x00" not in value)
    try:
        _require(len(value.encode("utf-8")) <= maximum)
    except UnicodeError:
        raise B2Error() from None
    return value


def _nullable_text(value, maximum=1024):
    if value is not None:
        _text(value, maximum)


def _name(value):
    _text(value, 63, empty=False)
    _require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", value, re.ASCII) is not None)


def _integer(value, maximum=_U32, minimum=1):
    _require(type(value) is int and minimum <= value <= maximum)


def _decimal(value, maximum, minimum=0, maxlen=1024):
    _text(value, maxlen, empty=False)
    _require(re.fullmatch(r"[0-9]+", value, re.ASCII) is not None)
    result = int(value)
    _require(minimum <= result <= maximum)
    return result


def _xmin(value):
    _decimal(value, _U32, maxlen=10)


def _snapshot(value):
    _text(value, empty=False)
    parts = value.split(":")
    _require(len(parts) == 3)
    low = _decimal(parts[0], _U64)
    high = _decimal(parts[1], _U64)
    _require(low <= high)
    ids = [] if parts[2] == "" else [_decimal(x, _U64) for x in parts[2].split(",")]
    _require(len(ids) == len(set(ids)) and all(low <= x < high for x in ids))


def _config(value):
    if value is None:
        return
    _require(type(value) is tuple and len(value) <= 256)
    keys = set()
    for item in value:
        _text(item, 8192)
        key, sep, unused = item.partition("=")
        _require(bool(sep) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", key, re.ASCII) is not None)
        folded = key.lower()
        _require(folded not in keys)
        keys.add(folded)


def _base64(value, size=None):
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise B2Error() from None
    _require(bool(decoded) and base64.b64encode(decoded).decode("ascii") == value)
    _require(size is None or len(decoded) == size)


def _verifier(value):
    if value is None:
        return
    _text(value, 1024, empty=False)
    if re.fullmatch(r"md5[0-9a-f]{32}", value, re.ASCII):
        return
    match = re.fullmatch(r"SCRAM-SHA-256\$([0-9]+):([^:$]+)\$([^:$]+):([^:$]+)", value, re.ASCII)
    _require(match is not None)
    iterations, salt, stored, server = match.groups()
    _decimal(iterations, _I32, 1)
    _base64(salt)
    _base64(stored, 32)
    _base64(server, 32)


class _Redacted:
    def __repr__(self):
        return "<" + type(self).__name__ + " redacted>"

    __str__ = __repr__


class SourcePhase(Enum):
    BEFORE = "before"
    DURING = "during"
    AFTER = "after"
    POSTCHECK = "postcheck"


@dataclass(frozen=True, repr=False)
class SourceReadScope(_Redacted):
    kind: str
    container_id: str
    system_identifier_sha256: str
    database_name: str
    connection_admin: str
    selected_role_names: tuple

    def __post_init__(self):
        _require(type(self.kind) is str and self.kind in ("central", "identity"))
        for value in (self.container_id, self.system_identifier_sha256):
            _text(value, 64)
            _require(re.fullmatch(r"[0-9a-f]{64}", value, re.ASCII) is not None)
        _name(self.database_name)
        _name(self.connection_admin)
        _require(type(self.selected_role_names) is tuple and 1 <= len(self.selected_role_names) <= 1024)
        for name in self.selected_role_names:
            _name(name)
        _require(len(set(self.selected_role_names)) == len(self.selected_role_names))
        _require(self.connection_admin in self.selected_role_names)


@dataclass(frozen=True, repr=False)
class SourceIdentity(_Redacted):
    database_name: str
    system_identifier: str
    server_version_num: int
    backend_pid: int
    snapshot_text: str
    read_only: str
    isolation: str
    timezone: str
    current_user: str
    session_user: str
    current_user_oid: int
    session_user_oid: int

    def __post_init__(self):
        _name(self.database_name)
        _decimal(self.system_identifier, _U64, 1)
        _integer(self.server_version_num, 169999, 160000)
        _integer(self.backend_pid, _I32)
        _snapshot(self.snapshot_text)
        _require(type(self.read_only) is str and self.read_only == "on")
        _require(type(self.isolation) is str and self.isolation == "repeatable read")
        _text(self.timezone, empty=False)
        _name(self.current_user)
        _name(self.session_user)
        _integer(self.current_user_oid)
        _integer(self.session_user_oid)
        _require(self.current_user == self.session_user and self.current_user_oid == self.session_user_oid)


@dataclass(frozen=True, repr=False)
class RoleState(_Redacted):
    oid: int
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
    auth_xmin: str

    def __post_init__(self):
        _integer(self.oid)
        _name(self.name)
        for value in (self.superuser, self.inherit, self.createrole, self.createdb,
                      self.login, self.replication, self.bypassrls):
            _require(type(value) is bool)
        _integer(self.connlimit, _I32, -1)
        _nullable_text(self.validuntil)
        _config(self.config)
        _verifier(self.verifier)
        _xmin(self.auth_xmin)


@dataclass(frozen=True, repr=False)
class DatabaseState(_Redacted):
    oid: int
    name: str
    owner_oid: int
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
    xmin: str

    def __post_init__(self):
        _integer(self.oid)
        _name(self.name)
        _integer(self.owner_oid)
        _name(self.owner_name)
        _integer(self.encoding_id, _I32, 0)
        _text(self.encoding_name, empty=False)
        _text(self.collate)
        _text(self.ctype)
        _require(type(self.provider) is str and self.provider in ("c", "i"))
        if self.provider == "c":
            _require(self.icu_locale is None and self.icu_rules is None)
        else:
            _text(self.icu_locale, empty=False)
            _nullable_text(self.icu_rules, 65536)
        for value in (self.recorded_version, self.actual_version, self.acl):
            _nullable_text(value)
        _integer(self.connlimit, _I32, -1)
        _require(type(self.istemplate) is bool and type(self.allowconn) is bool)
        _name(self.tablespace_name)
        _xmin(self.xmin)


@dataclass(frozen=True, repr=False)
class SettingState(_Redacted):
    database_oid: int
    role_oid: int
    role_name: str | None
    config: tuple | None
    xmin: str

    def __post_init__(self):
        _integer(self.database_oid, _U32, 0)
        _integer(self.role_oid, _U32, 0)
        if self.role_oid == 0:
            _require(self.role_name is None)
        else:
            _name(self.role_name)
        _config(self.config)
        _xmin(self.xmin)


def _items(value):
    result = [(field.name, getattr(value, field.name)) for field in fields(value)]
    if type(value) is SourceSample:
        result.append(("format", _FORMAT))
    return sorted(result)


def _chunks(value):
    """Canonical JSON stream without building a full private object tree."""
    if isinstance(value, _Redacted):
        yield "{"
        for index, (key, item) in enumerate(_items(value)):
            if index:
                yield ","
            yield json.dumps(key) + ":"
            yield from _chunks(item)
        yield "}"
    elif type(value) is tuple:
        yield "["
        for index, item in enumerate(value):
            if index:
                yield ","
            yield from _chunks(item)
        yield "]"
    else:
        if type(value) is SourcePhase:
            value = value.value
        yield json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _budget(value):
    total = 0
    for chunk in _chunks(value):
        total += len(chunk.encode("utf-8"))
        _require(total <= _MAX_BYTES)
    return total


def _tree(value):
    if isinstance(value, _Redacted):
        return {key: _tree(item) for key, item in _items(value)}
    if type(value) is tuple:
        return [_tree(item) for item in value]
    if type(value) is SourcePhase:
        return value.value
    return value


@dataclass(frozen=True, repr=False)
class SourceSample(_Redacted):
    scope: SourceReadScope
    phase: SourcePhase
    identity: SourceIdentity
    roles: tuple
    database: DatabaseState
    settings: tuple
    keeper_snapshot_token: str | None = None
    keeper_snapshot_text: str | None = None

    def __post_init__(self):
        _require(type(self.scope) is SourceReadScope and type(self.phase) is SourcePhase)
        _require(type(self.identity) is SourceIdentity and type(self.database) is DatabaseState)
        _require(type(self.roles) is tuple and 1 <= len(self.roles) <= 1024)
        _require(type(self.settings) is tuple and len(self.settings) <= 4096)
        _require(all(type(role) is RoleState for role in self.roles))
        _require(all(type(setting) is SettingState for setting in self.settings))
        scope, identity, database = self.scope, self.identity, self.database
        _require(identity.database_name == scope.database_name == database.name)
        _require(hashlib.sha256(identity.system_identifier.encode("utf-8")).hexdigest() == scope.system_identifier_sha256)
        _require(identity.current_user == scope.connection_admin == identity.session_user)
        names = {role.name: role for role in self.roles}
        oids = {role.oid: role for role in self.roles}
        _require(len(names) == len(oids) == len(self.roles) and 10 in oids)
        _require(set(names) == set(scope.selected_role_names) | {oids[10].name})
        _require(names[scope.connection_admin].oid == identity.current_user_oid == identity.session_user_oid)
        _require(database.owner_oid in oids and oids[database.owner_oid].name == database.owner_name)
        pairs, global_configs = set(), {}
        name_oids = {role.name: role.oid for role in self.roles}
        oid_names = {role.oid: role.name for role in self.roles}
        for setting in self.settings:
            _require(setting.database_oid in (0, database.oid))
            pair = (setting.database_oid, setting.role_oid)
            _require(pair not in pairs)
            pairs.add(pair)
            if setting.role_oid:
                _require(name_oids.get(setting.role_name, setting.role_oid) == setting.role_oid)
                _require(oid_names.get(setting.role_oid, setting.role_name) == setting.role_name)
                name_oids[setting.role_name] = setting.role_oid
                oid_names[setting.role_oid] = setting.role_name
            if setting.database_oid == 0:
                _require(setting.role_oid == 0 or setting.role_oid in oids)
                if setting.role_oid:
                    global_configs[setting.role_oid] = setting.config
        for role in self.roles:
            _require(role.config == global_configs.get(role.oid))
        if self.phase is SourcePhase.DURING:
            _text(self.keeper_snapshot_token, empty=False)
            _require(re.fullmatch(r"[0-9A-F]{8}-[0-9A-F]{8}-[1-9][0-9]*", self.keeper_snapshot_token, re.ASCII) is not None)
            _snapshot(self.keeper_snapshot_text)
            _require(identity.snapshot_text == self.keeper_snapshot_text)
        else:
            _require(self.keeper_snapshot_token is None and self.keeper_snapshot_text is None)
        _budget(self)

    def to_private_value(self):
        return _tree(self)

    def stable_private_value(self):
        result = self.to_private_value()
        for key in ("phase", "keeper_snapshot_token", "keeper_snapshot_text"):
            del result[key]
        for key in ("backend_pid", "snapshot_text"):
            del result["identity"][key]
        return result

    def safe_summary(self):
        digest = hashlib.sha256()
        for chunk in _chunks(self):
            digest.update(chunk.encode("utf-8"))
        stable = json.dumps(self.stable_private_value(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return {"format": _FORMAT, "kind": self.scope.kind, "phase": self.phase.value,
                "full_sha256": digest.hexdigest(), "stable_sha256": hashlib.sha256(stable).hexdigest(),
                "role_count": len(self.roles), "setting_count": len(self.settings),
                "data_only": True, "native_verified": False}


def _row(value, length):
    _require(type(value) in (tuple, list) and len(value) == length)
    return list(value)


def _rows(value, maximum):
    _require(type(value) in (tuple, list) and len(value) <= maximum)


def _array(value):
    if value is None:
        return None
    _require(type(value) in (tuple, list) and len(value) <= 256)
    return tuple(value)


def parse_source_sample(scope, phase, identity_row, role_rows, database_rows, setting_rows,
                        *, keeper_snapshot_token=None, keeper_snapshot_text=None):
    """Convert bounded fixed query rows; preserve exact DATA and source order."""
    _require(type(scope) is SourceReadScope and type(phase) is SourcePhase)
    identity = _row(identity_row, 12)
    identity[2] = _decimal(identity[2], 169999, 160000)
    identity = SourceIdentity(*identity)
    _rows(role_rows, 1024)
    _rows(database_rows, 1)
    _rows(setting_rows, 4096)
    _require(len(database_rows) == 1)
    roles, settings = [], []
    # Bound cumulative encoded payload while converting, before retaining a large tree.
    used = _budget(scope) + _budget(identity)
    for source in role_rows:
        row = _row(source, 14)
        row[11] = _array(row[11])
        role = RoleState(*row)
        used += _budget(role)
        _require(used <= _MAX_BYTES)
        roles.append(role)
    database = DatabaseState(*_row(database_rows[0], 19))
    used += _budget(database)
    _require(used <= _MAX_BYTES)
    for source in setting_rows:
        row = _row(source, 5)
        row[3] = _array(row[3])
        setting = SettingState(*row)
        used += _budget(setting)
        _require(used <= _MAX_BYTES)
        settings.append(setting)
    return SourceSample(scope, phase, identity, tuple(roles), database, tuple(settings),
                        keeper_snapshot_token, keeper_snapshot_text)
