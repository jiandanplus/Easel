"""Closed, bounded SQL-shaped observations; no runtime or database dependencies.

Context is supplied independently by the caller. In particular row_order_pins
must never be accepted from an archive: PostgreSQL row_to_json text ordering
cannot be reconstructed by assuming Python/JSON ordering or a DB collation.
"""
from dataclasses import dataclass
import base64
import copy
import hashlib
import json
import re

MAX_BYTES = 8 * 1024**2
CATALOG_WIDTHS = {"columns":17,"constraints":4,"functions":6,"enums":2,"triggers":5,
    "indexes":4,"policies":8,"namespaces":3,"default_acls":4,"database":6,
    "types":6,"relation_options":10,"sequence_definitions":13,"rules":4,
    "operators":5,"collations":9,"casts":7,"extensions":3,"event_triggers":5}
ROLE_WIDTHS = {"roles":11,"memberships":7,"ownership":4,"acls":7,"credentials":3}
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z", re.ASCII)
HASH = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
XMIN = re.compile(r"[0-9]{1,10}\Z", re.ASCII)
VERIFIER = re.compile(r"(?:md5[a-f0-9]{32}|SCRAM-SHA-256\$[1-9][0-9]{0,9}:[A-Za-z0-9+/]+={0,2}\$[A-Za-z0-9+/]{43}=[\:][A-Za-z0-9+/]{43}=)\Z", re.ASCII)


class B3Error(ValueError):
    """No offending values (including credential verifiers) in error messages."""


def require(condition):
    if not condition: raise B3Error("invalid B3 pure contract")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _tree(value, depth=0):
    require(depth <= 32)
    if type(value) is dict:
        require(all(type(k) is str for k in value))
        for v in value.values(): _tree(v, depth+1)
    elif type(value) is list:
        for v in value: _tree(v, depth+1)
    else:
        require(value is None or type(value) in (str, int, bool))
        if type(value) is int: require(-(2**63) <= value < 2**63)
        if type(value) is str: require("\x00" not in value)


def _bounded(value):
    try:
        _tree(value)
        require(len(canonical(value)) <= MAX_BYTES)
    except (RecursionError, UnicodeError, OverflowError, TypeError, ValueError):
        raise B3Error("invalid B3 pure contract") from None


def _keys(value, keys):
    require(type(value) is dict and value.keys() == set(keys))


def _ident(value):
    require(type(value) is str and IDENT.fullmatch(value) is not None)


def _pairs(items):
    result = {}
    for key, value in items:
        require(key not in result); result[key] = value
    return result


def _bad_number(value):
    raise B3Error("invalid B3 pure contract")


# Closed exact format_type spellings from public 001-020 and safe Logto 1.45
# type inventory. SQL aliases are deliberately not accepted as catalog names.
PINNED_ENUM_LABELS = {
    "agree_to_terms_policy": ("Automatic", "ManualRegistrationOnly", "Manual"),
    "application_type": ("Native", "SPA", "Traditional", "MachineToMachine", "Protected", "SAML"),
    "organization_invitation_status": ("Pending", "Accepted", "Expired", "Revoked"),
    "role_type": ("User", "MachineToMachine"),
    "sentinel_action_result": ("Success", "Failed"),
    "sentinel_decision": ("Undecided", "Allowed", "Blocked", "Challenge"),
    "sign_in_mode": ("SignIn", "Register", "SignInAndRegister"),
    "users_password_encryption_method": ("Argon2i", "Argon2id", "Argon2d", "SHA1", "SHA256", "MD5", "Bcrypt", "Legacy"),
}
VARCHAR_LENGTHS = (6, 16, 21, 25, 32, 64, 128, 255, 256, 512, 1024, 2048)
RAW_TYPES = frozenset({"smallint", "integer", "bigint", "text", "boolean", "json", "jsonb",
    "date", "timestamp with time zone", "timestamp without time zone", "bytea", "numeric(9,6)", "xid8"}
    | {f"character varying({n})" for n in VARCHAR_LENGTHS} | set(PINNED_ENUM_LABELS))


def _iso_temporal(value, kind):
    # PostgreSQL row_to_json uses ISO output independently of DateStyle.
    require(type(value) is str)
    if value in {"infinity", "-infinity"}: return
    pattern = r"([0-9]{4,7})-([0-9]{2})-([0-9]{2})"
    if kind != "date":
        pattern += r"T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:[.]([0-9]{1,6}))?"
        if kind == "timestamp with time zone":
            pattern += r"([+-])([0-9]{2}):([0-9]{2})(?::([0-9]{2}))?"
    match = re.fullmatch(pattern + r"( BC)?", value, re.ASCII)
    require(match is not None)
    year, month, day = map(int, match.group(1, 2, 3))
    bc = value.endswith(" BC")
    require(1 <= year <= (4714 if bc else (5874897 if kind == "date" else 294276)))
    # ISO astronomical year is 0 for 1 BC; civil PG spelling has no year 0.
    astronomical = 1-year if bc else year
    leap = astronomical % 4 == 0 and (astronomical % 100 != 0 or astronomical % 400 == 0)
    require(1 <= month <= 12)
    require(1 <= day <= (31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[month-1])
    if kind != "date":
        hour, minute, second = map(int, match.group(4, 5, 6))
        require(hour < 24 and minute < 60 and second < 60)
        if kind == "timestamp with time zone":
            require(int(match.group(9)) < 16 and int(match.group(10)) < 60)
            require(match.group(11) is None or int(match.group(11)) < 60)


def _raw_cell(cell, col):
    kind = col.type
    if kind in {"smallint", "integer", "bigint"}:
        bits = {"smallint":16, "integer":32, "bigint":64}[kind]
        require(type(cell) is int and -(2**(bits-1)) <= cell < 2**(bits-1))
    elif kind == "text": require(type(cell) is str)
    elif kind == "boolean": require(type(cell) is bool)
    elif kind in {"json", "jsonb"}: _tree(cell)
    elif kind.startswith("character varying("):
        require(type(cell) is str and len(cell) <= int(kind[18:-1]))
    elif kind == "date" or kind.startswith("timestamp "): _iso_temporal(cell, kind)
    elif kind == "bytea":
        # Both bytea_output forms are retained exactly, without decoding/coercion.
        require(type(cell) is str)
        if cell.startswith("\\x"):
            require(re.fullmatch(r"\\x(?:[0-9a-f]{2})*", cell, re.ASCII) is not None)
        else:
            require(re.fullmatch(r"(?:[ -\[\]-~]|\\\\|\\[0-3][0-7]{2})*", cell, re.ASCII) is not None)
    elif kind == "xid8":
        require(type(cell) is str and re.fullmatch(r"(?:0|[1-9][0-9]{0,19})", cell, re.ASCII) is not None)
        require(int(cell) < 2**64)
    elif kind == "numeric(9,6)":
        # row_to_json preserves scale (e.g. 1.000000), invoking B2's rejected
        # parse_float. No nonnull numeric compatibility is claimed, even for
        # synthetic ints. SQL NULL is handled before this function.
        require(False)
    elif kind in PINNED_ENUM_LABELS: require(type(cell) is str and cell in col.enum_labels)
    else: require(False)


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool
    enum_labels: tuple = ()


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple
    primary_key: tuple


@dataclass(frozen=True)
class RestoreProfile:
    name: str
    tables: tuple
    catalog_sha256: str
    source_kind: str
    evidence_mode: str


def parse_restore_profile(raw: bytes) -> RestoreProfile:
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES)
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_float=_bad_number, parse_constant=_bad_number)
        _bounded(value)
        _keys(value, {"format", "name", "tables", "catalog_sha256", "source_kind", "evidence_mode"})
        require((value["source_kind"], value["evidence_mode"]) in {("central", "full_raw_and_copy"), ("identity", "copy_only")})
        require(value["format"] == "easel-restore-profile-v1")
        require(type(value["name"]) is str and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", value["name"], re.ASCII))
        require(type(value["catalog_sha256"]) is str and HASH.fullmatch(value["catalog_sha256"]))
        require(type(value["tables"]) is list and 0 < len(value["tables"]) <= 1000)
        tables = []
        for table in value["tables"]:
            _keys(table, {"name", "columns", "primary_key"}); _ident(table["name"])
            require(type(table["columns"]) is list and 0 < len(table["columns"]) <= 512)
            columns = []
            for col in table["columns"]:
                require(type(col) is dict and type(col.get("type")) is str)
                kind = col["type"]
                _keys(col, {"name", "type", "nullable", "enum_labels"} if kind in PINNED_ENUM_LABELS else {"name", "type", "nullable"})
                _ident(col["name"])
                require(kind in RAW_TYPES and type(col["nullable"]) is bool)
                labels = col.get("enum_labels", [])
                if kind in PINNED_ENUM_LABELS:
                    require(type(labels) is list and tuple(labels) == PINNED_ENUM_LABELS[kind])
                columns.append(Column(col["name"], kind, col["nullable"], tuple(labels)))
            names = [col.name for col in columns]; require(len(set(names)) == len(names))
            pk = table["primary_key"]
            require(type(pk) is list and pk and all(type(k) is str and k in names for k in pk) and len(set(pk)) == len(pk))
            require(all(not col.nullable for col in columns if col.name in pk))
            tables.append(Table(table["name"], tuple(columns), tuple(pk)))
        names = [t.name for t in tables]; require(names == sorted(set(names)))
        return RestoreProfile(value["name"], tuple(tables), value["catalog_sha256"], value["source_kind"], value["evidence_mode"])
    except (TypeError, ValueError, KeyError, RecursionError, UnicodeError):
        raise B3Error("invalid B3 pure contract") from None


@dataclass(frozen=True)
class ComparisonContext:
    database_name: str
    role_names: tuple
    allowed_grantors: tuple
    profile: RestoreProfile
    row_order_pins: tuple = ()
    allowed_guc_keys: tuple = ()
    maximum_rows: int = 100000
    maximum_tables: int = 1000
    maximum_columns: int = 512
    source_kind: str = "central"
    evidence_mode: str = "full_raw_and_copy"

    def __post_init__(self):
        _ident(self.database_name)
        for items in (self.role_names, self.allowed_grantors, self.allowed_guc_keys):
            require(type(items) is tuple and len(set(items)) == len(items))
            for item in items: _ident(item)
        require(self.role_names and not set(self.role_names) & set(self.allowed_grantors))
        require(type(self.profile) is RestoreProfile)
        require((self.source_kind, self.evidence_mode) == (self.profile.source_kind, self.profile.evidence_mode))
        # Round-trip validates manually constructed dataclasses too.
        profile_raw = {"format":"easel-restore-profile-v1", "name":self.profile.name, "catalog_sha256":self.profile.catalog_sha256,
            "source_kind":self.profile.source_kind, "evidence_mode":self.profile.evidence_mode,
            "tables":[{"name":t.name,"columns":[dict({"name":c.name,"type":c.type,"nullable":c.nullable}, **({"enum_labels":list(c.enum_labels)} if c.enum_labels else {})) for c in t.columns],
                       "primary_key":list(t.primary_key)} for t in self.profile.tables]}
        require(parse_restore_profile(canonical(profile_raw)) == self.profile)
        for actual, cap in ((self.maximum_rows,100000),(self.maximum_tables,1000),(self.maximum_columns,512)):
            require(type(actual) is int and 0 < actual <= cap)
        require(len(self.profile.tables) <= self.maximum_tables)
        require(all(len(t.columns) <= self.maximum_columns for t in self.profile.tables))
        require(type(self.row_order_pins) is tuple)
        if self.evidence_mode == "copy_only": require(self.row_order_pins == ())
        else: require(tuple(p[0] for p in self.row_order_pins) == tuple(t.name for t in self.profile.tables))
        for table, pins in self.row_order_pins:
            require(type(pins) is tuple and len(pins) <= self.maximum_rows and len(set(pins)) == len(pins))
            require(all(type(pin) is str and HASH.fullmatch(pin) for pin in pins))


# Each positional descriptor follows capture.py SELECT order, including nullable
# LEFT JOIN column metadata. i is an identifier, s SQL/text, b bool, n int,
# l unique string array; '?' permits SQL NULL. SQL text is never evaluated.
CATALOG_TYPES = {
    "columns": "i i s i s? n? i? s? b? s? s? b b s? s? s? s?",
    "constraints": "i i i s", "functions": "i i s s i s?", "enums": "i l",
    "triggers": "i i i s s", "indexes": "i i i s", "policies": "i i i s l s s? s?",
    "namespaces": "i i s?", "default_acls": "s i s s", "database": "i i s? n b b",
    "types": "i i s i s? s?", "relation_options": "i i s s b i? i? l? s? s?",
    "sequence_definitions": "i i s n n n n n b i? i? i? s?", "rules": "i i i s",
    "operators": "i s s s s", "collations": "i i s b n s? s? s? s?",
    "casts": "i i i i s s s", "extensions": "i s i", "event_triggers": "i s s s l?",
}
ROLE_TYPES = {"roles":"i b b b b b b n s? b l?", "memberships":"i i b i b b s",
              "ownership":"i i i s?", "acls":"s s s i i s b", "credentials":"i s? s"}
CATALOG_ORDER = {key: tuple(range(n)) for key,n in {
    "constraints":3,"functions":3,"enums":1,"triggers":3,"indexes":3,"policies":3,
    "namespaces":1,"default_acls":3,"database":1,"types":2,"relation_options":2,
    "sequence_definitions":2,"rules":3,"operators":4,"collations":2,"casts":4,
    "extensions":1,"event_triggers":1}.items()}
CATALOG_ORDER["columns"] = (0,1,5)
ROLE_ORDER = {"roles":(0,),"memberships":(0,1,3),"ownership":(0,1),"acls":(0,1,2,3,4,5),"credentials":(0,)}


def _typed(value, descriptor):
    if value is None and descriptor.endswith("?"): return
    kind = descriptor.rstrip("?")
    if kind == "i": _ident(value)
    elif kind == "s": require(type(value) is str)
    elif kind == "b": require(type(value) is bool)
    elif kind == "n": require(type(value) is int)
    elif kind == "l":
        require(type(value) is list and all(type(x) is str for x in value) and len(set(value)) == len(value))
    else: require(False)


def _ordered(rows, indices):
    keys = [tuple((1, None) if row[i] is None else (0, row[i]) for i in indices) for row in rows]
    require(keys == sorted(keys) and len(set(keys)) == len(keys))


def _query_rows(value, widths, types, order, maximum):
    _keys(value, widths)
    for name, width in widths.items():
        rows = value[name]
        require(type(rows) is list and len(rows) <= maximum)
        for row in rows:
            require(type(row) is list and len(row) == width)
            for cell, descriptor in zip(row, types[name].split()): _typed(cell, descriptor)
        _ordered(rows, order[name])


def _owner(role, ctx, public_namespace=False):
    # Only the public namespace has PostgreSQL's builtin ownership indirection.
    # Relation/sequence/database/business owners remain explicit exported roles.
    require(role in ctx.role_names or (public_namespace and role == "pg_database_owner"))


def _verifier(value):
    if value is None: return
    require(len(value) <= 1024 and VERIFIER.fullmatch(value))
    if value.startswith("md5"): return
    _, iteration_salt, keys = value.split("$")
    iteration, salt = iteration_salt.split(":")
    require(0 < int(iteration) < 2**31)
    for encoded, key in [(salt, False)] + [(v, True) for v in keys.split(":")]:
        decoded = base64.b64decode(encoded, validate=True)
        require(base64.b64encode(decoded).decode("ascii") == encoded)
        require(len(decoded) == 32 if key else 0 < len(decoded) <= 768)


def _sequence(item, ctx):
    for field in ("schema","name","owner"): _ident(item[field])
    _owner(item["owner"], ctx)
    require(item["data_type"] in {"smallint","integer","bigint"})
    for field in ("start","increment","max","min","cache","last_value"): require(type(item[field]) is int)
    require(type(item["cycle"]) is bool and type(item["is_called"]) is bool)
    bits = {"smallint":16,"integer":32,"bigint":64}[item["data_type"]]
    require(-(2**(bits-1)) <= item["min"] < item["max"] < 2**(bits-1))
    require(item["min"] <= item["start"] <= item["max"] and item["min"] <= item["last_value"] <= item["max"])
    require(item["increment"] != 0 and item["cache"] > 0)
    dep = [item[k] for k in ("owned_schema","owned_table","owned_column","dependency")]
    if all(v is None for v in dep): return
    require(all(v is not None for v in dep) and item["dependency"] in {"a","i"})
    for v in dep[:3]: _ident(v)
    require(item["owned_schema"] in {"public","easel_backup_control"})
    if item["owned_schema"] == "public":
        tables = {t.name:t for t in ctx.profile.tables}
        require(item["owned_table"] in tables)
        require(item["owned_column"] in {c.name for c in tables[item["owned_table"]].columns})


def validate_observation(value: dict, context: ComparisonContext) -> dict:
    try:
        require(type(context) is ComparisonContext)
        _bounded(value); _keys(value, {"schema","roles","rows","copy","sequences"})
        schema, roles = value["schema"], value["roles"]
        _query_rows(schema, CATALOG_WIDTHS, CATALOG_TYPES, CATALOG_ORDER, context.maximum_rows)
        _query_rows(roles, ROLE_WIDTHS, ROLE_TYPES, ROLE_ORDER, context.maximum_rows)
        require(len(schema["database"]) == 1 and schema["database"][0][0] == context.database_name)
        pinned_catalog = copy.deepcopy(schema)
        pinned_catalog["database"][0][0] = "<trusted-database>"
        require(digest(pinned_catalog) == context.profile.catalog_sha256)
        require({r[0] for r in roles["roles"]} == set(context.role_names))
        require({r[0] for r in roles["credentials"]} == set(context.role_names))
        for row in roles["roles"]:
            require(-1 <= row[7] < 2**31)
            if row[10] is not None:
                keys = []
                for setting in row[10]:
                    key, sep, val = setting.partition("=")
                    require(sep == "=" and key in context.allowed_guc_keys); keys.append(key)
                require(len(set(keys)) == len(keys))
        grantors = set(context.role_names) | set(context.allowed_grantors)
        for row in roles["memberships"]:
            require(row[0] in context.role_names and row[1] in context.role_names and row[3] in grantors)
            require(XMIN.fullmatch(row[6]) and int(row[6]) < 2**32)
        for row in roles["credentials"]:
            _verifier(row[1])
            require(XMIN.fullmatch(row[2]) and int(row[2]) < 2**32)
        for row in roles["ownership"]: _owner(row[2], context)
        for row in roles["acls"]:
            require(row[2] in {"relation","column","function","type","namespace","default","database"})
            require(row[3] in grantors and row[4] in set(context.role_names)|{"PUBLIC","pg_database_owner"})
            if row[2] == "database": require(row[0] == "" and row[1] == context.database_name)
        for name, index in (("columns",3),("functions",4),("namespaces",1),("default_acls",1),("database",1),("types",3)):
            for row in schema[name]: _owner(row[index], context, name == "namespaces" and row[0] == "public")
        require(-1 <= schema["database"][0][3] < 2**31)
        for row in schema["policies"]: require(set(row[4]) <= set(context.role_names)|{"public","PUBLIC","pg_database_owner"})
        tables = {t.name:t for t in context.profile.tables}
        _keys(value["rows"], tables if context.evidence_mode == "full_raw_and_copy" else {}); _keys(value["copy"], {"public."+t for t in tables})
        observed_columns = {name:[] for name in tables}
        for row in schema["columns"]:
            if row[0] == "public" and row[2] in {"r","p"}:
                require(row[1] in tables)
                if row[6] is not None: observed_columns[row[1]].append((row[6], row[7], not row[8]))
        pins = dict(context.row_order_pins)
        for table, spec in tables.items():
            for col in spec.columns:
                if col.enum_labels:
                    require([col.type, list(col.enum_labels)] in schema["enums"])
            expected = [(c.name, c.type, c.nullable) for c in spec.columns]
            require(observed_columns[table] == expected)
            if context.evidence_mode == "full_raw_and_copy":
                rows = value["rows"][table]; require(type(rows) is list and len(rows) <= context.maximum_rows)
                seen = set()
                for row in rows:
                    _keys(row, {c.name for c in spec.columns})
                    for col in spec.columns:
                        cell = row[col.name]
                        # row_to_json collapses SQL NULL and stored JSON scalar null
                        # to the same wire None. JSON families must admit that value
                        # even under NOT NULL; actual COPY binds the distinction later.
                        if cell is None:
                            require(col.nullable or col.type in {"json", "jsonb"})
                            continue
                        _raw_cell(cell, col)
                    key = canonical([row[c] for c in spec.primary_key]); require(key not in seen); seen.add(key)
                require(tuple(digest(row) for row in rows) == pins[table])
            inv = value["copy"]["public."+table]; _keys(inv, {"columns","count","rows_sha256"})
            copy_columns = [r[6] for r in schema["columns"]
                if r[0] == "public" and r[1] == table and r[2] in {"r", "p"} and r[6] is not None and r[16] == ""]
            require(inv["columns"] == copy_columns)
            require(type(inv["count"]) is int and 0 <= inv["count"] <= context.maximum_rows)
            if context.evidence_mode == "full_raw_and_copy": require(inv["count"] == len(rows))
            require(type(inv["rows_sha256"]) is str and HASH.fullmatch(inv["rows_sha256"]))
        require(type(value["sequences"]) is list and len(value["sequences"]) <= context.maximum_rows)
        sequence_keys = []
        for item in value["sequences"]:
            _keys(item, {"schema","name","owner","data_type","start","increment","max","min","cache","cycle",
                         "owned_schema","owned_table","owned_column","dependency","last_value","is_called"})
            _sequence(item, context); sequence_keys.append((item["schema"],item["name"]))
        require(sequence_keys == sorted(set(sequence_keys)))
        definitions = {(r[0],r[1]):r for r in schema["sequence_definitions"]}
        require(set(sequence_keys) == set(definitions))
        for item in value["sequences"]:
            row = definitions[(item["schema"],item["name"])]
            require(row == [item[k] for k in ("schema","name","data_type","start","increment","min","max","cache","cycle",
                                           "owned_schema","owned_table","owned_column","dependency")])
        return copy.deepcopy(value)
    except (TypeError, ValueError, KeyError, AttributeError, RecursionError):
        raise B3Error("invalid B3 pure contract") from None
