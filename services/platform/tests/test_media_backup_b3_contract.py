"""Complete synthetic SQL-shaped observations; no fixture/product imports."""
import copy
import hashlib
import dataclasses
import json
import unittest
from platform_service.media_backup_b3.contracts import (B3Error, ComparisonContext, parse_restore_profile,
                                validate_observation, CATALOG_WIDTHS, ROLE_WIDTHS)


def fixture(database="source_db"):
    profile_spec = {
        "format": "easel-restore-profile-v1", "source_kind":"central", "evidence_mode":"full_raw_and_copy", "name": "synthetic",
        "tables": [{"name": "items", "columns": [
            {"name": "id", "type": "integer", "nullable": False},
            {"name": "label", "type": "text", "nullable": True}],
            "primary_key": ["id"]}],
    }
    schema = {
        "columns": [["public", "items", "r", "app", None, 1, "id", "integer", True, "", None, False, False, "-", "p", "", ""],
                    ["public", "items", "r", "app", None, 2, "label", "text", False, "", None, False, False, "default", "x", "", ""]],
        "constraints": [["public", "items", "items_pkey", "PRIMARY KEY (id)"]],
        "functions": [["public", "fn", "", "CREATE FUNCTION fn() RETURNS void LANGUAGE sql SET search_path TO public AS 'SELECT 1'", "app", None]],
        "enums": [["state", ["open", "closed"]]],
        "triggers": [["public", "items", "trg", "CREATE TRIGGER trg", "O"]],
        "indexes": [["public", "items", "items_pkey", "CREATE UNIQUE INDEX items_pkey"]],
        "policies": [["public", "items", "policy", "PERMISSIVE", ["app"], "ALL", None, None]],
        "namespaces": [["public", "app", None]],
        "default_acls": [["public", "app", "r", "{app=arwdDxt/app}"]],
        "database": [[database, "app", None, -1, False, True]],
        "types": [["public", "state", "e", "app", None, None]],
        "relation_options": [["public", "items", "p", "d", False, "heap", None, None, None, None]],
        "sequence_definitions": [["public", "items_id_seq", "bigint", 1, 1, 1, 99, 1, False, "public", "items", "id", "a"]],
        "rules": [["public", "items", "rule", "CREATE RULE rule"]],
        "operators": [["public", "===", "integer", "integer", "fn(integer,integer)"]],
        "collations": [["public", "coll", "c", True, -1, "C", "C", None, None]],
        "casts": [["public", "state", "pg_catalog", "text", "fn(state)", "e", "f"]],
        "extensions": [["plpgsql", "1.0", "pg_catalog"]],
        "event_triggers": [["evt", "ddl_command_end", "O", "fn()", None]],
    }
    pinned_schema = copy.deepcopy(schema); pinned_schema["database"][0][0] = "<trusted-database>"
    profile_spec["catalog_sha256"] = hashlib.sha256(json.dumps(pinned_schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    profile = parse_restore_profile(json.dumps(profile_spec).encode())
    context = ComparisonContext(database, ("app", "reader"), ("admin",), profile,
                                allowed_guc_keys=("search_path",),
                                row_order_pins=(("items", (hashlib.sha256(b'{"id":1,"label":"hello"}').hexdigest(),)),))
    roles = {"roles": [["app", False, True, False, False, True, False, -1, None, False, ["search_path=public"]],
                        ["reader", False, True, False, False, False, False, -1, None, False, None]],
             "memberships": [["reader", "app", False, "admin", True, True, "12"]],
             "ownership": [["public", "items", "app", None]],
             "acls": [["public", "items", "relation", "app", "reader", "SELECT", False]],
             "credentials": [["app", "md5" + "a" * 32, "13"], ["reader", None, "14"]]}
    value = {"schema": schema, "roles": roles, "rows": {"items": [{"id": 1, "label": "hello"}]},
             "copy": {"public.items": {"columns": ["id", "label"], "count": 1, "rows_sha256": "a" * 64}},
             "sequences": [{"schema": "public", "name": "items_id_seq", "owner": "app", "data_type": "bigint",
                            "start": 1, "increment": 1, "max": 99, "min": 1, "cache": 1, "cycle": False,
                            "owned_schema": "public", "owned_table": "items", "owned_column": "id", "dependency": "a",
                            "last_value": 1, "is_called": True}]}
    return value, context


class ContractTests(unittest.TestCase):
    def test_complete_query_shapes(self):
        value, ctx = fixture()
        self.assertEqual(validate_observation(value, ctx), value)
        self.assertEqual(set(value["schema"]), set(CATALOG_WIDTHS))
        self.assertEqual(set(value["roles"]), set(ROLE_WIDTHS))

    def test_closed_profile_json_and_bounds(self):
        for raw in [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1.5}', b'[]', b' ' * (8*1024**2+1), b'{"x":' + b'['*33 + b'0' + b']'*33 + b'}']:
            with self.subTest(raw=raw[:50]), self.assertRaises(B3Error):
                parse_restore_profile(raw)

    def test_malformed_observations_fail(self):
        value, ctx = fixture()
        mutations = [lambda v: v.update(extra={}),
                     lambda v: v["schema"].update(extra=[]),
                     lambda v: v["roles"]["credentials"][0].append("extra"),
                     lambda v: v["roles"]["credentials"][0].__setitem__(1, "md5bad"),
                     lambda v: v["roles"]["credentials"][0].__setitem__(2, "not_decimal"),
                     lambda v: v["schema"]["database"][0].__setitem__(3, True),
                     lambda v: v["schema"]["database"][0].__setitem__(3, 1.5),
                     lambda v: v["schema"]["database"][0].__setitem__(0, "untrusted"),
                     lambda v: v["roles"]["roles"].reverse(),
                     lambda v: v["roles"]["roles"].append(v["roles"]["roles"][0]),
                     lambda v: v["roles"]["roles"][0].__setitem__(10, ["evil=code"]),
                     lambda v: v["roles"]["roles"][0].__setitem__(10, ["search_path=a", "search_path=b"]),
                     lambda v: v["rows"]["items"][0].update(extra=0),
                     lambda v: v["rows"].clear(),
                     lambda v: v["rows"]["items"].append({"id": 1, "label": "different"}),
                     lambda v: v["copy"]["public.items"].__setitem__("count", 0),
                     lambda v: v["sequences"][0].update(extra=0),
                     lambda v: v["sequences"][0].__setitem__("min", 100),
                     lambda v: v["schema"]["columns"][0].__setitem__(6, "wrong")]
        for mutation in mutations:
            damaged = copy.deepcopy(value); mutation(damaged)
            with self.subTest(mutation=mutations.index(mutation)), self.assertRaises(B3Error):
                validate_observation(damaged, ctx)

    def test_limits_independent_and_fail_closed(self):
        value, ctx = fixture()
        for changes in ({"maximum_rows": True}, {"maximum_rows": 100001}, {"maximum_columns": 513}, {"maximum_tables": 1001}):
            with self.assertRaises(B3Error):
                ComparisonContext(ctx.database_name, ctx.role_names, ctx.allowed_grantors, ctx.profile, **changes)

    def test_row_order_pin_permutation_and_omission(self):
        value, ctx = fixture()
        rows = [{"id": 1, "label": "hello"}, {"id": 2, "label": "world"}]
        pins = tuple(hashlib.sha256(json.dumps(r, sort_keys=True, separators=(",", ":")).encode()).hexdigest() for r in rows)
        ctx = ComparisonContext(ctx.database_name, ctx.role_names, ctx.allowed_grantors, ctx.profile,
                                row_order_pins=(("items", pins),), allowed_guc_keys=("search_path",))
        value["rows"]["items"] = rows; value["copy"]["public.items"]["count"] = 2
        validate_observation(value, ctx)
        value["rows"]["items"].reverse()
        with self.assertRaises(B3Error): validate_observation(value, ctx)
        value["rows"]["items"] = rows[:1]; value["copy"]["public.items"]["count"] = 1
        with self.assertRaises(B3Error): validate_observation(value, ctx)

    def test_legitimate_columnless_relation_nulls_remain_null(self):
        value, ctx = fixture()
        row = ["public", "view_empty", "v", "app", None, None, None, None, None, None, None,
               False, False, None, None, None, None]
        value["schema"]["columns"].append(row)
        pin = copy.deepcopy(value["schema"]); pin["database"][0][0] = "<trusted-database>"
        pin_hash = hashlib.sha256(json.dumps(pin, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        ctx = dataclasses.replace(ctx, profile=dataclasses.replace(ctx.profile, catalog_sha256=pin_hash))
        result = validate_observation(value, ctx)
        self.assertEqual(result["schema"]["columns"][-1], row)

    def test_wide_boundaries_and_nested_bad_values(self):
        value, ctx = fixture()
        for mutate in [lambda v: v["schema"]["constraints"][0].__setitem__(3, {"sql":"bad"}),
                       lambda v: v["schema"]["policies"][0].__setitem__(4, ["app", "app"]),
                       lambda v: v["schema"]["database"].append(v["schema"]["database"][0]),
                       lambda v: v["schema"]["columns"][0].__setitem__(1, "x"*64),
                       lambda v: v["roles"]["credentials"][0].__setitem__(2, "4294967296"),
                       lambda v: v["roles"]["credentials"][0].__setitem__(1, "x"*1025),
                       lambda v: v["copy"]["public.items"].__setitem__("columns", ["id"]),
                       lambda v: v["schema"]["sequence_definitions"][0].__setitem__(5, 99),
                       lambda v: v["roles"]["memberships"][0].__setitem__(3, "outsider")]:
            damaged = copy.deepcopy(value); mutate(damaged)
            with self.assertRaises(B3Error): validate_observation(damaged, ctx)

    def test_profile_is_closed_and_manually_built_profiles_revalidated(self):
        base = {"format":"easel-restore-profile-v1", "source_kind":"central", "evidence_mode":"full_raw_and_copy", "name":"test", "catalog_sha256":"a"*64, "tables":[
            {"name":"items", "columns":[{"name":"id", "type":"integer", "nullable":False}], "primary_key":["id"]}]}
        for mutate in [lambda p: p.update(extra=0),
                       lambda p: p["tables"][0].update(extra=0),
                       lambda p: p["tables"][0]["columns"][0].update(extra=0),
                       lambda p: p["tables"][0]["columns"][0].__setitem__("nullable", 1),
                       lambda p: p["tables"][0]["columns"].append(p["tables"][0]["columns"][0]),
                       lambda p: p["tables"][0].__setitem__("primary_key", [])]:
            damaged = copy.deepcopy(base); mutate(damaged)
            with self.assertRaises(B3Error): parse_restore_profile(json.dumps(damaged).encode())

    def test_scram_verifier_encoding_and_md5_forms(self):
        value, ctx = fixture()
        good = "SCRAM-SHA-256$4096:c2FsdA==$" + "A"*43 + "=:" + "A"*43 + "="
        value["roles"]["credentials"][0][1] = good
        validate_observation(value, ctx)
        for bad in [good.replace("c2FsdA==", "A"), good.replace("4096", "9999999999"),
                    good.replace("4096", "0"), "md5"+"g"*32, good+";SQL"]:
            value["roles"]["credentials"][0][1] = bad
            with self.assertRaises(B3Error): validate_observation(value, ctx)


# Public schema-derived examples are literals: no B2/product/fixture imports.
ENUM_LABELS = {
    "agree_to_terms_policy": ["Automatic", "ManualRegistrationOnly", "Manual"],
    "application_type": ["Native", "SPA", "Traditional", "MachineToMachine", "Protected", "SAML"],
    "organization_invitation_status": ["Pending", "Accepted", "Expired", "Revoked"],
    "role_type": ["User", "MachineToMachine"],
    "sentinel_action_result": ["Success", "Failed"],
    "sentinel_decision": ["Undecided", "Allowed", "Blocked", "Challenge"],
    "sign_in_mode": ["SignIn", "Register", "SignInAndRegister"],
    "users_password_encryption_method": ["Argon2i", "Argon2id", "Argon2d", "SHA1", "SHA256", "MD5", "Bcrypt", "Legacy"],
}
VARCHAR_LENGTHS = (6, 16, 21, 25, 32, 64, 128, 255, 256, 512, 1024, 2048)


def repin(value, ctx):
    """Independent synthetic authority built by test, never runtime inference."""
    catalog = copy.deepcopy(value["schema"])
    catalog["database"][0][0] = "<trusted-database>"
    hashed = lambda v: hashlib.sha256(json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return dataclasses.replace(ctx,
        profile=dataclasses.replace(ctx.profile, catalog_sha256=hashed(catalog)),
        row_order_pins=tuple((t.name, tuple(hashed(r) for r in value["rows"][t.name])) for t in ctx.profile.tables))


def typed_fixture(kind, cell, nullable=False, labels=None):
    value, ctx = fixture()
    columns = [{"name":"id", "type":"integer", "nullable":False},
               {"name":"value", "type":kind, "nullable":nullable}]
    if labels is not None: columns[1]["enum_labels"] = labels
    profile = {"format":"easel-restore-profile-v1", "source_kind":"central", "evidence_mode":"full_raw_and_copy", "name":"synthetic-types",
        "catalog_sha256":ctx.profile.catalog_sha256,
        "tables":[{"name":t.name, "columns":[{"name":c.name, "type":c.type, "nullable":c.nullable} for c in t.columns],
                   "primary_key":list(t.primary_key)} for t in ctx.profile.tables] +
                 [{"name":"typed_values", "columns":columns, "primary_key":["id"]}]}
    for index, col in enumerate(columns, 1):
        value["schema"]["columns"].append(["public", "typed_values", "r", "app", None,
            index, col["name"], col["type"], not col["nullable"], "", None, False, False, "-", "p", "", ""])
    if labels is not None:
        value["schema"]["enums"] = sorted([[kind, list(labels)], ["state", ["open", "closed"]]])
    value["rows"]["typed_values"] = [{"id":1, "value":cell}]
    value["copy"]["public.typed_values"] = {"columns":["id", "value"], "count":1, "rows_sha256":"b"*64}
    ctx = dataclasses.replace(ctx, profile=parse_restore_profile(json.dumps(profile).encode()),
        row_order_pins=ctx.row_order_pins + (("typed_values", ("0"*64,)),))
    return value, repin(value, ctx)


class ReviewCorrectionTests(unittest.TestCase):
    def test_all_public_type_families_and_nullable_two_tables(self):
        examples = {"smallint":-32768, "integer":2147483647, "bigint":9223372036854775807,
            "text":"中文", "boolean":True, "json": {"a":[True, 1, "s", None]},
            "jsonb":[True, 1, "s", {"a":False}], "date":"2024-02-29",
            "timestamp with time zone":"2024-02-29T23:59:59.123456+00:00",
            "timestamp without time zone":"2024-02-29T23:59:59.123456",
            "bytea":r"\x00ff", "xid8":"18446744073709551615"}
        examples.update({f"character varying({n})":"中"*n for n in VARCHAR_LENGTHS})
        examples.update({k:v[0] for k,v in ENUM_LABELS.items()})
        for kind, cell in examples.items():
            for nullable, sample in [(False, cell), (True, cell), (True, None)]:
                with self.subTest(kind=kind, nullable=nullable, null=sample is None):
                    value, ctx = typed_fixture(kind, sample, nullable, ENUM_LABELS.get(kind))
                    self.assertEqual(validate_observation(value, ctx), value)
                    self.assertEqual(len(value["rows"]), 2)
        for kind in ("json", "jsonb"):
            for cell in (True, False, 0, "str", [1, None], {"nested": [False]}):
                value, ctx = typed_fixture(kind, cell)
                self.assertEqual(validate_observation(value, ctx), value)

    def test_matching_pin_integer_range_edges(self):
        for kind, bits in [("smallint",16), ("integer",32), ("bigint",64)]:
            for cell in (-(2**(bits-1)), 2**(bits-1)-1):
                value, ctx = typed_fixture(kind, cell)
                validate_observation(value, ctx)
            for cell in (-(2**(bits-1))-1, 2**(bits-1), True, "1", 1.0):
                with self.subTest(kind=kind, cell=cell):
                    value, ctx = typed_fixture(kind, cell)
                    with self.assertRaises(B3Error): validate_observation(value, ctx)

    def test_matching_pin_invalid_representations(self):
        bad = {"text":[1, True, []], "boolean":[0, "true", []],
            "json":[1.5, {"nested":[float("nan")]}], "jsonb":[1.5, {"nested":[float("inf")]}],
            "date":["2023-02-29", "2024-13-01", "2024-01-01T00:00:00", 1],
            "timestamp with time zone":["2024-01-01T00:00:00", "2024-02-30T00:00:00+00:00", "2024-01-01T24:00:00+00:00", 1],
            "timestamp without time zone":["2024-01-01T00:00:00Z", "2024-01-01", "2024-01-01T00:60:00", 1],
            "bytea":[r"\x0", r"\xgg", r"\400", 1],
            "numeric(9,6)":[0, 999, 1000, -1000, 1.5, "1.000000", True],
            "xid8":["18446744073709551616", "-1", "01", 1, True]}
        bad.update({f"character varying({n})":["中"*(n+1), 1, True] for n in VARCHAR_LENGTHS})
        bad.update({k:["invalid", v[0].lower(), 1] for k,v in ENUM_LABELS.items()})
        for kind, cells in bad.items():
            for cell in cells + ([] if kind in {"json", "jsonb"} else [None]):
                with self.subTest(kind=kind, cell=repr(cell)):
                    # NaN has a deterministic JSON test pin; runtime rejects it globally.
                    value, ctx = typed_fixture(kind, cell, labels=ENUM_LABELS.get(kind))
                    with self.assertRaises(B3Error): validate_observation(value, ctx)

    def test_exact_type_spelling_and_enum_authority(self):
        for kind in ("varchar(6)", "character varying(7)", "timestamptz", "numeric", "evil", "INTEGER"):
            with self.subTest(kind=kind), self.assertRaises(B3Error): typed_fixture(kind, "x")
        value, ctx = typed_fixture("application_type", "Native", labels=ENUM_LABELS["application_type"])
        value["schema"]["enums"][0][1] = ["Native", "wrong"]
        ctx = repin(value, ctx)
        with self.assertRaises(B3Error): validate_observation(value, ctx)
        value, ctx = typed_fixture("integer", 1)
        value["schema"]["columns"][-1][7] = "bigint"
        ctx = repin(value, ctx)
        with self.assertRaises(B3Error): validate_observation(value, ctx)

    def test_explicit_grantor_sets_membership_and_acl(self):
        for group, position in [("memberships",3), ("acls",3)]:
            value, ctx = fixture()
            value["roles"][group][0][position] = "pg_database_owner"
            with self.subTest(group=group), self.assertRaises(B3Error): validate_observation(value, ctx)
            explicit = dataclasses.replace(ctx, allowed_grantors=ctx.allowed_grantors + ("pg_database_owner",))
            validate_observation(value, explicit)
        value, ctx = fixture()
        value["schema"]["namespaces"][0][1] = "pg_database_owner"
        validate_observation(value, repin(value, ctx))

    def test_cross_table_omission_with_unchanged_independent_authority(self):
        value, ctx = typed_fixture("boolean", True)
        for group, name in [("rows","typed_values"), ("copy","public.typed_values")]:
            damaged = copy.deepcopy(value); del damaged[group][name]
            with self.assertRaises(B3Error): validate_observation(damaged, ctx)
        value["rows"]["typed_values"] = []
        value["copy"]["public.typed_values"]["count"] = 0
        with self.assertRaises(B3Error): validate_observation(value, ctx)

    def test_actual_catalog_and_role_order_families_with_matching_pins(self):
        from platform_service.media_backup_b3.contracts import CATALOG_ORDER, ROLE_ORDER
        for group, families in [("schema", CATALOG_ORDER), ("roles", ROLE_ORDER)]:
            for family, indices in families.items():
                if family == "database": continue  # exact singleton separately covered
                value, ctx = fixture()
                rows = value[group][family]
                if family == "columns":
                    # Two actual columns already have increasing attnum.
                    pass
                elif family in ("roles", "credentials"):
                    pass  # two distinct exported roles already present
                else:
                    row = copy.deepcopy(rows[0]); i = indices[-1]
                    row[i] = row[i] + "_z"
                    if family == "functions": row[1] = "fn_z"; row[2] = ""
                    if family == "operators": row[3] = "text"
                    if family == "casts": row[3] = "varchar"
                    if family == "memberships": row[i] = "admin_z"; ctx = dataclasses.replace(ctx, allowed_grantors=("admin", "admin_z"))
                    if family == "sequence_definitions":
                        item = copy.deepcopy(value["sequences"][0]); item["name"] = row[1]; value["sequences"].append(item)
                    rows.append(row)
                with self.subTest(group=group, family=family):
                    validate_observation(value, repin(value, ctx))
                    rows.reverse()
                    with self.assertRaises(B3Error): validate_observation(value, repin(value, ctx))
                    rows.reverse(); rows.append(copy.deepcopy(rows[-1]))
                    with self.assertRaises(B3Error): validate_observation(value, repin(value, ctx))

    def test_numeric_scale_is_explicitly_unsupported_nonnull(self):
        value, ctx = typed_fixture("numeric(9,6)", None, nullable=True)
        self.assertEqual(validate_observation(value, ctx), value)

    def test_temporal_bytea_and_all_enum_labels_positive_forms(self):
        samples = {"date":["infinity", "-infinity", "0001-01-01 BC"],
            "timestamp with time zone":["infinity", "2024-01-01T01:02:03-05:30", "2024-01-01T01:02:03+00:19:32"],
            "timestamp without time zone":["-infinity", "0001-01-01T00:00:00 BC"],
            "bytea":["", r"\x", r"abc\\def\000\377"]}
        samples.update(ENUM_LABELS)
        for kind, cells in samples.items():
            for cell in cells:
                with self.subTest(kind=kind, cell=cell):
                    value, ctx = typed_fixture(kind, cell, labels=ENUM_LABELS.get(kind))
                    self.assertEqual(validate_observation(value, ctx), value)

    def test_integer_overflow_is_rejected_with_matching_pin(self):
        # Kept separate so unsupported other descriptors cannot hide R2.
        value, ctx = fixture()
        value["rows"]["items"][0]["id"] = 2147483648
        with self.assertRaises(B3Error): validate_observation(value, repin(value, ctx))

    def test_namespace_builtin_ownership_does_not_authorize_business_owners(self):
        for group, family, position in [("roles", "ownership", 2), ("schema", "columns", 3),
                ("schema", "database", 1), ("schema", "functions", 4), ("schema", "types", 3),
                ("schema", "default_acls", 1)]:
            value, ctx = fixture()
            value[group][family][0][position] = "pg_database_owner"
            with self.subTest(group=group, family=family), self.assertRaises(B3Error):
                validate_observation(value, repin(value, ctx))
        value, ctx = fixture()
        value["sequences"][0]["owner"] = "pg_database_owner"
        with self.assertRaises(B3Error): validate_observation(value, ctx)


class EvidenceModeTests(unittest.TestCase):
    def identity(self):
        value, ctx = typed_fixture("numeric(9,6)", None, nullable=True)
        value["rows"] = {}
        ctx = dataclasses.replace(ctx, source_kind="identity", evidence_mode="copy_only", row_order_pins=(),
            profile=dataclasses.replace(ctx.profile, source_kind="identity", evidence_mode="copy_only"))
        return value, ctx

    def test_identity_copy_only_has_complete_catalog_and_copy(self):
        value, ctx = self.identity()
        value["copy"]["public.items"]["count"] = 100000
        value["copy"]["public.typed_values"]["count"] = 0
        self.assertEqual(validate_observation(value, ctx), value)
        from platform_service.media_backup_b3.compare import compare_observations
        self.assertTrue(compare_observations(value, copy.deepcopy(value), ctx, ctx).equal)

    def test_mode_pairs_and_central_downgrade_are_closed(self):
        value, ctx = fixture()
        for changes in ({"source_kind":"identity", "evidence_mode":"copy_only", "row_order_pins":()}, {"source_kind":"central", "evidence_mode":"copy_only"},
                {"source_kind":"identity", "evidence_mode":"full_raw_and_copy"},
                {"source_kind":"unknown"}, {"evidence_mode":"unknown"}):
            with self.subTest(changes=changes), self.assertRaises(B3Error): dataclasses.replace(ctx, **changes)
        for mode in ("rows", "pins"):
            value, ctx = self.identity()
            if mode == "rows":
                value["rows"] = {"items":[]}
                with self.assertRaises(B3Error): validate_observation(value, ctx)
            else:
                with self.assertRaises(B3Error): dataclasses.replace(ctx, row_order_pins=(("items", ()),))
        value, ctx = fixture()
        value["rows"] = {}
        with self.assertRaises(B3Error): validate_observation(value, ctx)

    def test_identity_copy_is_exact_and_counts_are_bounded(self):
        for mutate in (lambda v:v["copy"].pop("public.items"),
                lambda v:v["copy"].update(extra={}),
                lambda v:v["copy"]["public.items"].__setitem__("columns", ["id"]),
                lambda v:v["copy"]["public.items"].__setitem__("rows_sha256", "bad")):
            value, ctx = self.identity(); mutate(value)
            with self.assertRaises(B3Error): validate_observation(value, ctx)
        for count in (-1, 100001, True, 1.0, "1"):
            value, ctx = self.identity(); value["copy"]["public.items"]["count"] = count
            with self.subTest(count=count), self.assertRaises(B3Error): validate_observation(value, ctx)
        value, ctx = self.identity()
        value["schema"]["columns"][-1][7] = "text"
        schema = copy.deepcopy(value["schema"]);schema["database"][0][0] = "<trusted-database>"
        pin = hashlib.sha256(json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        ctx = dataclasses.replace(ctx, profile=dataclasses.replace(ctx.profile, catalog_sha256=pin))
        with self.assertRaises(B3Error): validate_observation(value, ctx)

    def test_comparison_rejects_cross_context_kind_and_mode_swap(self):
        from platform_service.media_backup_b3.compare import compare_observations
        central, central_ctx = typed_fixture("numeric(9,6)", None, nullable=True)
        identity, identity_ctx = self.identity()
        with self.assertRaises(B3Error): compare_observations(central, identity, central_ctx, identity_ctx)
        with self.assertRaises(B3Error): compare_observations(identity, central, identity_ctx, central_ctx)

    def test_copy_columns_follow_pinned_attnum_and_skip_generated(self):
        value, ctx = self.identity()
        value["schema"]["columns"][1][16] = "s"
        schema = copy.deepcopy(value["schema"]); schema["database"][0][0] = "<trusted-database>"
        pin = hashlib.sha256(json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        ctx = dataclasses.replace(ctx, profile=dataclasses.replace(ctx.profile, catalog_sha256=pin))
        value["copy"]["public.items"]["columns"] = ["id"]
        self.assertEqual(validate_observation(value, ctx), value)
        value["copy"]["public.items"]["columns"] = ["id", "label"]
        with self.assertRaises(B3Error): validate_observation(value, ctx)


class JsonNullReviewTests(unittest.TestCase):
    def test_json_null_top_level_matches_pins_for_notnull_and_nullable(self):
        for kind in ("json", "jsonb"):
            for nullable in (False, True):
                with self.subTest(kind=kind, nullable=nullable):
                    value, ctx = typed_fixture(kind, None, nullable=nullable)
                    self.assertEqual(validate_observation(value, ctx), value)
                    # The raw JSON wire cannot distinguish stored JSON null
                    # from SQL NULL. Later actual COPY must bind that distinction.
                    self.assertIsNone(value["rows"]["typed_values"][0]["value"])

    def test_nonjson_notnull_none_still_rejects_with_matching_pins(self):
        for kind in ("text", "integer", "boolean", "date", "bytea", "xid8"):
            with self.subTest(kind=kind):
                value, ctx = typed_fixture(kind, None, nullable=False)
                with self.assertRaises(B3Error): validate_observation(value, ctx)
                nullable_value, nullable_ctx = typed_fixture(kind, None, nullable=True)
                self.assertEqual(validate_observation(nullable_value, nullable_ctx), nullable_value)
