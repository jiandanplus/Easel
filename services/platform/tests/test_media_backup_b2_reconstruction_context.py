"""Synthetic DATA-contract tests; no provider, process or target fixtures."""
import base64
import builtins
from contextlib import ExitStack
import io
import os
import socket
import subprocess
import time
import types
from unittest import mock
import dataclasses
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
MODULE = "platform_service.media_backup_b2.reconstruction_context"
SPEC = importlib.util.find_spec(MODULE)
if SPEC is not None:
    from platform_service.media_backup_b2 import reconstruction_context as c
from platform_service.media_backup_b2.contracts import B2Error


class GuardedAcquisition(RuntimeError):
    """An intercepted acquisition attempt, before the patched callable runs."""


def audit_module_execution(source):
    # Read source at the caller and compile BEFORE installing any blockers.
    code = compile(source, "<synthetic-reconstruction-context>", "exec")
    name = c.__package__ + "._synthetic_inertness_audit"
    module = types.ModuleType(name)
    module.__package__ = c.__package__
    original_import = builtins.__import__

    def cached_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level:
            if level != 1 or name != "contracts" or globals.get("__package__") != c.__package__:
                raise GuardedAcquisition("uncached import")
        elif name not in sys.modules:
            raise GuardedAcquisition("uncached import")
        return original_import(name, globals, locals, fromlist, level)

    # Bounded Python-callable interception, NOT a security sandbox or syscall trace.
    targets = [(builtins, "open"), (io, "open")]
    targets += [(os, n) for n in (
        "open", "stat", "lstat", "listdir", "scandir", "readlink", "access",
        "mkdir", "makedirs", "remove", "unlink", "rename", "replace", "rmdir",
        "system", "popen", "fork", "forkpty", "posix_spawn", "posix_spawnp",
        "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
        "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe")
        if hasattr(os, n)]
    targets += [(subprocess, n) for n in ("Popen", "run", "call", "check_call", "check_output")]
    targets += [(socket, n) for n in ("socket", "socketpair", "create_connection", "getaddrinfo", "gethostbyname")]
    targets += [(time, n) for n in ("time", "time_ns", "monotonic", "monotonic_ns", "perf_counter", "perf_counter_ns", "process_time", "thread_time", "sleep", "gmtime", "localtime", "ctime", "strftime")]
    with mock.patch.dict(sys.modules, {name: module}):
        with ExitStack() as stack:
            for owner, attr in targets:
                stack.enter_context(mock.patch.object(owner, attr, side_effect=GuardedAcquisition(attr)))
            stack.enter_context(mock.patch.object(builtins, "__import__", side_effect=cached_import))
            exec(code, module.__dict__)
    return module


def fixtures():
    scope = c.SourceReadScope("central", "a" * 64, hashlib.sha256(b"123456789").hexdigest(),
                              "source_db", "operator", ("operator", "owner", "runtime", "controller", "pg_database_owner"))
    identity = ["source_db", "123456789", "160004", 123, "10:20:11,15", "on",
                "repeatable read", "Asia/Shanghai", "operator", "operator", 42, 42]
    roles = [[oid, name, True, True, False, False, True, False, -1,
              "infinity", False, None, None, "000123"]
             for oid, name in [(10, "postgres"), (42, "operator"), (43, "owner"),
                               (44, "runtime"), (45, "controller"), (6171, "pg_database_owner")]]
    database = [[1234, "source_db", 43, "owner", 8, "LATIN1", "de_DE.ISO8859-1",
                 "de_DE.ISO8859-1", "c", None, None, "2.35", "2.36", "{owner=CTc/owner}",
                 -1, False, True, "custom_space", "000045"]]
    settings = [[1234, 0, None, ["search_path=public, custom", "custom.option=秘密"], "0009"],
                [1234, 888, "external_role", [], "10"]]
    return scope, identity, roles, database, settings


def sample(parts=None, phase=None, **kw):
    scope, identity, roles, database, settings = parts or fixtures()
    return c.parse_source_sample(scope, phase or c.SourcePhase.BEFORE,
                                 identity, roles, database, settings, **kw)


@unittest.skipUnless(SPEC is not None, "contract absent; explicit availability assertion covers RED")
class ContractTests(unittest.TestCase):
    def bad(self, fn):
        with self.assertRaises(B2Error) as caught:
            fn()
        self.assertEqual(str(caught.exception), "invalid_backup")
        self.assertEqual(caught.exception.args, ("invalid_backup",))

    def test_complete_field_order(self):
        fields = {
            c.SourceIdentity: "database_name system_identifier server_version_num backend_pid snapshot_text read_only isolation timezone current_user session_user current_user_oid session_user_oid",
            c.RoleState: "oid name superuser inherit createrole createdb login replication connlimit validuntil bypassrls config verifier auth_xmin",
            c.DatabaseState: "oid name owner_oid owner_name encoding_id encoding_name collate ctype provider icu_locale icu_rules recorded_version actual_version acl connlimit istemplate allowconn tablespace_name xmin",
            c.SettingState: "database_oid role_oid role_name config xmin",
            c.SourceSample: "scope phase identity roles database settings keeper_snapshot_token keeper_snapshot_text",
        }
        for cls, names in fields.items():
            self.assertEqual([f.name for f in dataclasses.fields(cls)], names.split())

    def test_full_libc_values_and_unknown_settings_retained(self):
        value = sample()
        self.assertEqual(value.database.encoding_name, "LATIN1")
        self.assertEqual(value.database.collate, "de_DE.ISO8859-1")
        self.assertEqual(value.database.tablespace_name, "custom_space")
        self.assertEqual((value.database.recorded_version, value.database.actual_version), ("2.35", "2.36"))
        self.assertEqual(value.roles[0].name, "postgres")
        self.assertNotIn("postgres", value.scope.selected_role_names)
        self.assertEqual(value.identity.current_user_oid, 42)
        self.assertEqual(value.settings[1].role_name, "external_role")
        self.assertEqual(value.settings[0].config[1], "custom.option=秘密")
        self.assertEqual(value.roles[0].validuntil, "infinity")
        self.assertEqual(value.roles[0].auth_xmin, "000123")

    def test_icu_exact_rules_and_null_empty(self):
        for rules in (None, "", "&a < b\n&c < d"):
            p = fixtures()
            p[3][0][8:11] = ["i", "und-u-ks-level2", rules]
            result = sample(p)
            self.assertEqual(result.database.icu_rules, rules)
            self.assertEqual(result.to_private_value()["database"]["icu_rules"], rules)

    def test_verifiers_and_exact_validuntil(self):
        b64 = lambda raw: base64.b64encode(raw).decode("ascii")
        scram = "SCRAM-SHA-256$4096:" + b64(b"salt") + "$" + b64(b"k" * 32) + ":" + b64(b"s" * 32)
        for verifier in (None, "md5" + "a" * 32, scram):
            p = fixtures()
            p[2][0][12] = verifier
            p[2][0][9] = "2026-01-01 12:34:56+08"
            result = sample(p)
            self.assertEqual(result.roles[0].verifier, verifier)
            self.assertEqual(result.roles[0].validuntil, p[2][0][9])

    def test_frozen_detached_and_redacted(self):
        p = fixtures()
        value = sample(p)
        private = value.to_private_value()
        private["settings"][0]["config"].append("other=changed")
        p[4][0][3].append("other=changed")
        self.assertEqual(len(value.settings[0].config), 2)
        for obj in (value, value.scope, value.identity, value.database, *value.roles, *value.settings):
            self.assertNotIn("source_db", repr(obj))
            self.assertNotIn("operator", str(obj))
            with self.assertRaises(dataclasses.FrozenInstanceError):
                obj.unknown = 1
        summary = value.safe_summary()
        self.assertTrue(summary["data_only"])
        self.assertFalse(summary["native_verified"])
        self.assertNotIn("秘密", json.dumps(summary))
        self.assertEqual(summary["full_sha256"], hashlib.sha256(json.dumps(value.to_private_value(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest())

    def test_phase_and_stable_projection(self):
        initial = sample()
        for phase in (c.SourcePhase.BEFORE, c.SourcePhase.AFTER, c.SourcePhase.POSTCHECK, c.SourcePhase.DURING):
            p = fixtures()
            p[1][3] += 1
            p[1][4] = "21:30:"
            kw = {} if phase != c.SourcePhase.DURING else dict(keeper_snapshot_token="00000003-0000001B-1", keeper_snapshot_text="21:30:")
            value = sample(p, phase, **kw)
            self.assertEqual(initial.stable_private_value(), value.stable_private_value())
            self.assertNotEqual(initial.to_private_value(), value.to_private_value())
        for kw in ({}, {"keeper_snapshot_token": "00000003-0000001B-1"},
                   {"keeper_snapshot_token": "x'; SQL", "keeper_snapshot_text": "10:20:11,15"},
                   {"keeper_snapshot_token": "00000003-0000001B-1", "keeper_snapshot_text": "10:20:"}):
            self.bad(lambda: sample(phase=c.SourcePhase.DURING, **kw))
        self.bad(lambda: sample(keeper_snapshot_token="00000003-0000001B-1", keeper_snapshot_text="10:20:11,15"))
        self.bad(lambda: c.parse_source_sample(fixtures()[0], "before", *fixtures()[1:]))

    def test_stable_protected_fields(self):
        base = sample().stable_private_value()
        for part, row, col, val in [(1, None, 7, "UTC"), (2, 0, 9, None), (2, 0, 13, "124"),
                                     (3, 0, 11, None), (3, 0, 12, "2.37"), (3, 0, 18, "46"),
                                     (4, 0, 4, "11"), (4, 1, 3, None)]:
            p = fixtures()
            target = p[part] if row is None else p[part][row]
            target[col] = val
            self.assertNotEqual(base, sample(p).stable_private_value())
        p = fixtures()
        p[4][0][3].reverse()
        self.assertNotEqual(base, sample(p).stable_private_value())

    def test_scope_invalid_shapes(self):
        s = fixtures()[0]
        for field, value in [("kind", "other"), ("container_id", "A" * 64), ("system_identifier_sha256", "x" * 64),
                             ("database_name", "bad-name"), ("connection_admin", "other"), ("selected_role_names", []),
                             ("selected_role_names", ("operator", "operator")), ("selected_role_names", ()),
                             ("selected_role_names", tuple("r" + str(i) for i in range(1025)))]:
            self.bad(lambda: dataclasses.replace(s, **{field: value}))

    def test_identity_invalid_values(self):
        for col, values in {0: ["wrong"], 1: ["0", "18446744073709551616", "1a"],
                            2: ["150000", "170000", 160004, True], 3: [True, 0, -1],
                            4: ["20:10:", "10:20:11,11", "10:20:9", "10:20:20", "a:b:", "10:20:11,", "10:18446744073709551616:"],
                            5: ["off", True], 6: ["read committed"], 7: ["", "x\x00y", "\ud800", "中" * 342],
                            8: ["owner"], 9: ["owner"], 10: [True, 0, 10], 11: [43]}.items():
            for val in values:
                with self.subTest(col=col, val=repr(val)):
                    p = fixtures(); p[1][col] = val
                    self.bad(lambda: sample(p))

    def test_role_invalid_values(self):
        for col, values in {0: [0, True, 2**32], 1: ["bad-role", "x" * 64],
                            2: [0, "true"], 3: [1], 4: [None], 5: [1], 6: [0], 7: [1],
                            8: [-2, True, 2**31], 9: [123, "x" * 1025], 10: [1],
                            11: [["bad"], ["a=1", "A=2"], ["a=\x00"], ["a=" + "x" * 8192], ["a=1"] * 257],
                            12: ["", "md5" + "A" * 32, "secret", "SCRAM-SHA-256$0:c2FsdA==$a:b", "x" * 1025],
                            13: ["-1", "4294967296", 1, "00000000000"]}.items():
            for val in values:
                p = fixtures(); p[2][0][col] = val
                self.bad(lambda: sample(p))

    def test_malformed_scram(self):
        key = base64.b64encode(b"k" * 32).decode()
        for salt, a, b, iterations in [("", key, key, "1"), ("c2FsdA=", key, key, "1"),
                                      ("c2FsdB==", key, key, "1"), ("c2FsdA==", "YQ==", key, "1"),
                                      ("c2FsdA==", key, key, "2147483648"), ("c2FsdA==", key, key + ":x", "1")]:
            p = fixtures(); p[2][0][12] = f"SCRAM-SHA-256${iterations}:{salt}${a}:{b}"
            self.bad(lambda: sample(p))

    def test_role_set_and_owner_consistency(self):
        for mutation in (lambda p: p[2].pop(0), lambda p: p[2].pop(2),
                         lambda p: p[2].append(p[2][0][:]),
                         lambda p: p[2][0].__setitem__(0, 11),
                         lambda p: p[2][1].__setitem__(0, 46),
                         lambda p: p[3][0].__setitem__(2, 999),
                         lambda p: p[3][0].__setitem__(3, "runtime")):
            p = fixtures(); mutation(p); self.bad(lambda: sample(p))
        p = fixtures(); extra = p[2][0][:]; extra[0:2] = [999, "extra"]; p[2].append(extra)
        self.bad(lambda: sample(p))

    def test_database_invalid_values(self):
        for col, values in {0: [True, 0], 1: ["wrong"], 2: [True], 3: ["runtime"],
                            4: [True, -1], 5: [""], 6: [None], 7: [None], 8: ["x"],
                            9: ["und"], 10: [""], 11: [True], 12: [0], 13: [False],
                            14: [-2, True], 15: [1], 16: [0], 17: ["bad-name"], 18: ["-1"]}.items():
            for val in values:
                p = fixtures(); p[3][0][col] = val; self.bad(lambda: sample(p))
        for locale, rules in [(None, None), ("", ""), ("und", "x" * 65537)]:
            p = fixtures(); p[3][0][8:11] = ["i", locale, rules]; self.bad(lambda: sample(p))

    def test_settings_references_and_global_agreement(self):
        for row in ([999, 0, None, None, "1"], [0, 888, "external_role", None, "1"],
                    [1234, 0, "owner", None, "1"], [1234, 43, "runtime", None, "1"],
                    [1234, 888, None, None, "1"], [0, 42, "operator", [], "1"]):
            p = fixtures(); p[4].append(row); self.bad(lambda: sample(p))
        p = fixtures(); p[4].append(p[4][0][:]); self.bad(lambda: sample(p))
        p = fixtures(); p[2][1][11] = []; self.bad(lambda: sample(p))
        p[4].append([0, 42, "operator", [], "1"])
        self.assertEqual(sample(p).roles[1].config, ())
        p[4][-1][3] = None; self.bad(lambda: sample(p))

    def test_row_shapes_and_collection_bounds(self):
        for index in (1, 2, 3, 4):
            for row in (None, {}, "private", [1], [None] * 30):
                p = list(fixtures()); p[index] = row if index == 1 else [row]
                self.bad(lambda: sample(p))
        for index, count in ((2, 1025), (3, 2), (4, 4097)):
            p = list(fixtures()); p[index] = p[index] * count; self.bad(lambda: sample(p))

    def test_constructor_checks_and_no_mutable_inputs(self):
        value = sample()
        for record, field, val in [(value.identity, "backend_pid", True), (value.roles[0], "superuser", 1),
                                    (value.database, "provider", "x"), (value.settings[0], "xmin", "bad"),
                                    (value, "roles", [*value.roles]), (value, "settings", []),
                                    (value, "identity", None), (value, "keeper_snapshot_token", "bad")]:
            self.bad(lambda: dataclasses.replace(record, **{field: val}))
        self.bad(lambda: dataclasses.replace(value.roles[0], config=["a=1"]))

    def test_aggregate_limit(self):
        p = list(fixtures())
        config = [f"guc.k{i}=" + "x" * 8100 for i in range(256)]
        p[4] = [[1234, 1000 + i, f"role{i}", config, "1"] for i in range(5)]
        self.bad(lambda: sample(p))

    def test_snapshot_xip_order_is_source_data(self):
        p = fixtures(); p[1][4] = "10:20:15,11"
        self.assertEqual(sample(p).identity.snapshot_text, "10:20:15,11")

    def test_decimal_text_uses_text_budget_and_numeric_range(self):
        p = list(fixtures())
        exact = "0" * 30 + "123456789"
        p[1][1] = exact
        p[0] = dataclasses.replace(p[0], system_identifier_sha256=hashlib.sha256(exact.encode()).hexdigest())
        p[1][2] = "0" * 30 + "160004"
        try:
            result = sample(p)
        except B2Error:
            self.fail("in-range decimal source spelling rejected below the text budget")
        self.assertEqual(result.identity.system_identifier, exact)
        self.assertEqual(result.identity.server_version_num, 160004)

    def test_null_empty_order_and_all_private_fields(self):
        value = sample()
        private = value.to_private_value()
        for field in dataclasses.fields(value):
            self.assertIn(field.name, private)
        for name, record in (("identity", value.identity), ("database", value.database), ("scope", value.scope)):
            for field in dataclasses.fields(record):
                actual = private[name][field.name]
                expected = getattr(record, field.name)
                self.assertEqual(actual, list(expected) if type(expected) is tuple else expected)
        for name, records in (("roles", value.roles), ("settings", value.settings)):
            for item, record in zip(private[name], records):
                for field in dataclasses.fields(record):
                    expected = getattr(record, field.name)
                    self.assertEqual(item[field.name], list(expected) if type(expected) is tuple else expected)
        for config in (None, [], ["A.X=2", "a.y=1=2"]):
            p = fixtures(); p[4][0][3] = config
            self.assertEqual(sample(p).settings[0].config, None if config is None else tuple(config))
        p = fixtures(); p[2].reverse(); p[4].reverse()
        self.assertEqual([r.name for r in sample(p).roles], [r[1] for r in p[2]])
        self.assertEqual([r.role_oid for r in sample(p).settings], [r[1] for r in p[4]])

    def test_constructor_cross_row_checks_match_parser(self):
        v = sample()
        for field, replacement in [("identity", dataclasses.replace(v.identity, database_name="wrong")),
                                   ("roles", tuple(r for r in v.roles if r.oid != 10)),
                                   ("database", dataclasses.replace(v.database, owner_name="runtime")),
                                   ("settings", v.settings + (c.SettingState(999, 0, None, None, "1"),)),
                                   ("phase", "before")]:
            self.bad(lambda: dataclasses.replace(v, **{field: replacement}))

    def test_max_roles_includes_external_bootstrap(self):
        p = list(fixtures())
        selected = tuple(["operator"] + [f"r{i}" for i in range(1023)])
        p[0] = dataclasses.replace(p[0], selected_role_names=selected)
        template = p[2][0]
        p[2] = [template[:]] + [[42 if name == "operator" else 10000 + i, name, *template[2:]] for i, name in enumerate(selected)]
        self.bad(lambda: sample(p))
        selected = selected[:-1]
        p[0] = dataclasses.replace(p[0], selected_role_names=selected)
        p[2].pop()
        p[3][0][2:4] = [42, "operator"]
        self.assertEqual(len(sample(p).roles), 1024)

    def test_settings_unknown_pairs_consistent_across_rows(self):
        for row in ([1234, 889, "external_role", None, "1"], [1234, 888, "another", None, "1"],
                    [1234, 999, "operator", None, "1"]):
            p = fixtures(); p[4].append(row); self.bad(lambda: sample(p))
        p = fixtures(); p[4].append([0, 0, None, ["custom.anything=value"], "1"])
        self.assertEqual(sample(p).settings[-1].database_oid, 0)

    def test_utf8_limits_not_character_limits(self):
        p = fixtures(); p[4][0][3] = ["x=" + "中" * 2730]
        self.assertEqual(len(sample(p).settings[0].config[0].encode()), 8192)
        p[4][0][3][0] += "中"
        self.bad(lambda: sample(p))
        p = fixtures(); p[3][0][8:11] = ["i", "und", "中" * 21845 + "x"]
        self.assertEqual(len(sample(p).database.icu_rules.encode()), 65536)
        p[3][0][10] += "x"; self.bad(lambda: sample(p))

    def test_stable_icu_rules_and_null_empty_changes(self):
        p = fixtures(); p[3][0][8:11] = ["i", "und", None]
        original = sample(p).stable_private_value()
        for rules in ("", "&a < b"):
            p[3][0][10] = rules
            self.assertNotEqual(original, sample(p).stable_private_value())

    def test_module_import_is_inert_by_synthetic_execution(self):
        source = Path(c.__file__).read_text()
        audited = audit_module_execution(source)
        self.assertEqual(audited.SourcePhase.BEFORE.value, "before")
        self.assertIs(audited.B2Error, B2Error)
        self.assertNotIn(audited.__name__, sys.modules)

    def test_inertness_audit_intercepts_in_memory_mutants(self):
        source = Path(c.__file__).read_text()
        # Every mutation is appended in memory; no mutant source is written.
        mutants = {
            "assignment": "probe = open('synthetic-never-opened')",
            "annotated_assignment": "probe: object = open('synthetic-never-opened')",
            "class_body": "class Probe:\n    value = open('synthetic-never-opened')",
            "default_argument": "def probe(value=open('synthetic-never-opened')):\n    pass",
            "decorator": "@open('synthetic-never-opened')\ndef probe():\n    pass",
            "clock": "import time\nprobe = time.time()",
            "stat": "import os\nprobe = os.stat('synthetic-never-statted')",
            "process": "import subprocess\nprobe = subprocess.Popen(['synthetic-never-spawned'])",
            "socket": "import socket\nprobe = socket.socket()",
        }
        for label, mutation in mutants.items():
            with self.subTest(path=label):
                with self.assertRaises(GuardedAcquisition):
                    audit_module_execution(source + "\n" + mutation + "\n")
        # Guards and temporary dataclass registration are restored after failure.
        self.assertNotIn(c.__package__ + "._synthetic_inertness_audit", sys.modules)

    def test_full_sample_preserves_scram_leading_zero_iteration_spelling(self):
        verifier = ("SCRAM-SHA-256$04096:c2FsdA==$"
                    + base64.b64encode(b"k" * 32).decode("ascii") + ":"
                    + base64.b64encode(b"s" * 32).decode("ascii"))
        parts = fixtures()
        parts[2][0][12] = verifier
        value = sample(parts)
        self.assertEqual(value.roles[0].verifier, verifier)
        self.assertEqual(value.to_private_value()["roles"][0]["verifier"], verifier)
        self.assertEqual(value.stable_private_value()["roles"][0]["verifier"], verifier)


class AvailabilityTests(unittest.TestCase):
    def test_data_contract_exists(self):
        self.assertIsNotNone(SPEC, "private reconstruction DATA contract is not implemented")


if __name__ == "__main__":
    unittest.main(verbosity=2)
