"""Pure screening: independently fixed policies, no product/package initialization."""
import dataclasses
import hashlib
import importlib
import json
from pathlib import Path
import sys
import types
import unittest

SRC = Path(__file__).resolve().parents[1] / "src" / "platform_service"
# Deliberately bypass product __init__ and its runtime dependencies.
for name, path in (("platform_service", SRC), ("platform_service.media_backup_b3", SRC / "media_backup_b3")):
    if name not in sys.modules:
        module = types.ModuleType(name)
        module.__path__ = [str(path)]
        sys.modules[name] = module
try:
    ds = importlib.import_module("platform_service.media_backup_b3.dump_screen")
except ModuleNotFoundError as exc:
    if exc.name != "platform_service.media_backup_b3.dump_screen": raise
    ds = None

HEADER = b""";
; Archive created at 2026-11-03 10:35:29 UTC
;     dbname: fixture_db
;     TOC Entries: 7
;     Compression: gzip
;     Dump Version: 1.15-0
;     Format: CUSTOM
;     Integer: 4 bytes
;     Offset: 8 bytes
;     Dumped from database version: 16.15 (Debian 16.15-1.pgdg13+2)
;     Dumped by pg_dump version: 16.15 (Debian 16.15-1.pgdg13+2)
;
;
; Selected TOC Entries:
;
"""
RECORDS = (("SCHEMA", "-", "public", "pg_database_owner"),
           ("FUNCTION", "public", "lock_identity(text, integer)", "fixture_role"),
           ("TABLE DATA", "public", "items", "fixture_role"))
TOC = HEADER + b"5; 2615 2200 SCHEMA - public pg_database_owner\n9; 1255 77 FUNCTION public lock_identity(text, integer) fixture_role\n15; 0 80 TABLE DATA public items fixture_role\n"
DATA_HEADER = b"""--
-- PostgreSQL database dump
--

\\restrict easelB2SchemaComparison20261030

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;
"""
BODY = b"COPY public.items (id, payload) FROM stdin;\n1\t\\N\n2\tSQLlike\\tvalue\\nnext\n\\.\nCOPY public.empty (id) FROM stdin;\n\\.\nSELECT pg_catalog.setval('public.items_id_seq', -7, true);\n"
END = b"\\unrestrict easelB2SchemaComparison20261030\n"
DATA = DATA_HEADER + BODY + END

class DumpScreenTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(ds, "pure dump screening module must exist")
        self.toc_policy = ds.TocPolicy(RECORDS)
        self.data_policy = ds.DataPolicy((("public.items", ("id", "payload")), ("public.empty", ("id",))), (("public.items_id_seq", -7, True),))

    def rejected(self, func, *args):
        with self.assertRaises(ds.B3Error) as caught: func(*args)
        self.assertEqual(str(caught.exception), "invalid B3 dump screening")

    def test_toc_exact_logical_inventory_not_header_or_oid_equality(self):
        result = ds.parse_toc(TOC, self.toc_policy)
        self.assertEqual((result.entry_count, result.header_entries), (3, 7))
        self.assertEqual(result.sha256, hashlib.sha256(TOC).hexdigest())
        changed = TOC.replace(b"1255 77", b"4294967295 0")
        self.assertEqual(ds.parse_toc(changed, self.toc_policy).entry_count, 3)
        lines = TOC[len(HEADER):].splitlines(keepends=True)
        self.assertEqual(ds.parse_toc(HEADER+b"".join(reversed(lines)), self.toc_policy).entry_count, 3)
        self.rejected(ds.parse_toc, TOC, ds.TocPolicy(RECORDS, expected_header_entries=8))

    def test_toc_mutations_rejected(self):
        mutations = [(b"9;", b"5;"), (b"9;", b"0;"), (b"9;", b"2147483648;"),
            (b"1255 77", b"-1 77"), (b"1255 77", b"1255 4294967296"),
            (b"FUNCTION public", b"EVENT TRIGGER public"), (b"TABLE DATA public", b"FOREIGN TABLE public"),
            (b"lock_identity(text, integer)", b"lock_identity(text)"),
            (b"fixture_role", b"other_role"), (b"FUNCTION public", b"FUNCTION private"),
            (b"CUSTOM", b"TAR"), (b"16.15", b"17.15"), (b"1.15-0", b"1.16-0")]
        for old, new in mutations:
            with self.subTest(new=new): self.rejected(ds.parse_toc, TOC.replace(old, new, 1), self.toc_policy)
        for raw in (TOC+b"20; 0 80 TABLE DATA public items fixture_role\n", TOC.rsplit(b"15;",1)[0], TOC+b"; ignored?\n", TOC+b"\x00", TOC+b"\xff", TOC[:-1], b"x"*1048577, HEADER+b"1"*8193+b"\n"):
            self.rejected(ds.parse_toc, raw, self.toc_policy)

    def test_policy_validation_closed_and_deeply_immutable(self):
        for records in ([RECORDS[0]], (list(RECORDS[0]),), (("DATABASE", "-", "fixture_db", "fixture_role"),), (("SCHEMA", "-", "private", "fixture_role"),), (("TABLE", "public", "items", "-"),), (("TABLE", "public", "items", "pg_database_owner"),)):
            self.rejected(ds.TocPolicy, records)
        self.rejected(ds.TocPolicy, RECORDS, True)
        for tables, sequences in (([], ()), ((("public.items", ["id"]),), ()), ((("private.items", ("id",)),), ()), ((("public.items", ("id", "id")),), ()), ((), (("public.s", True, True),)), ((), (("public.s", 1, 1),)), ((), (("public.s", 2**63, False),))):
            self.rejected(ds.DataPolicy, tables, sequences)
        with self.assertRaises(dataclasses.FrozenInstanceError): self.toc_policy.expected_records = ()
        self.assertNotIn("fixture_role", repr(self.toc_policy))
        self.assertNotIn("items", repr(self.data_policy))

    def test_multiset_duplicates_preserved(self):
        # Explicitly expected logical multiplicity is retained, never collapsed to a set.
        extra = b"16; 0 81 TABLE DATA public items fixture_role\n"
        policy = ds.TocPolicy(RECORDS + (RECORDS[-1],))
        self.assertEqual(ds.parse_toc(TOC+extra, policy).entry_count, 4)
        self.rejected(ds.parse_toc, TOC, policy)
        self.rejected(ds.parse_toc, TOC+extra, self.toc_policy)

    def test_data_layout_and_sql_looking_rows_are_bytes(self):
        result = ds.screen_data_sql(DATA, self.data_policy)
        self.assertEqual((result.table_count, result.sequence_count, result.row_count), (2, 1, 2))
        self.assertEqual(result.sha256, hashlib.sha256(DATA).hexdigest())
        for row in (b"-- DROP ROLE x;\tvalue\n", b"COPY evil\tvalue\n", b"\\connect other\tvalue\n", "中文\t多行\\n文本\n".encode(), b"\\N\t\\t\\n\\\\\n"):
            raw = DATA.replace(b"1\t\\N\n", row)
            self.assertEqual(ds.screen_data_sql(raw, self.data_policy).row_count, 2)
        self.assertNotIn("payload", repr(result))

    def test_data_rejects_commands_headers_scope_and_coverage(self):
        mutations = [(b"SET lock_timeout = 0;", b"SET lock_timeout = 1;"),
            (b"SET lock_timeout = 0;", b"SET lock_timeout = 0;\nSET lock_timeout = 0;"),
            (b"SET lock_timeout = 0;\n", b""),
            (b"SET lock_timeout = 0;", b"SET lock_timeout = 0; DROP ROLE x;"),
            (b"COPY public.items", b"COPY private.items"), (b"(id, payload)", b"(payload, id)"),
            (b"FROM stdin;", b"FROM PROGRAM 'x';"), (b"1\t\\N", b"1"),
            (b"-7, true", b"-7, false"), (b"-7, true", b"-07, true"),
            (b"-7, true", b"-0, true"), (b"-7, true", b"9223372036854775808, true"),
            (b"public.items_id_seq", b"public.unknown"),
            (b"\\unrestrict", b"\\connect"), (b"\\restrict", b"\\include"),
            (b"COPY public.empty (id) FROM stdin;\n\\.\n", b""),
            (b"SELECT pg_catalog.setval('public.items_id_seq', -7, true);\n", b""),
            (b"COPY public.empty (id) FROM stdin;\n\\.\n", b"COPY public.empty (id) FROM stdin;\n\\.\nCOPY public.empty (id) FROM stdin;\n\\.\n")]
        for old, new in mutations:
            with self.subTest(new=new): self.rejected(ds.screen_data_sql, DATA.replace(old, new, 1), self.data_policy)
        for raw in (DATA+b"ALTER TABLE public.items DISABLE TRIGGER ALL;\n", DATA+b"\\restrict easelB2SchemaComparison20261030\n", DATA.replace(END, b""), DATA[:-1], DATA+b"\x00", DATA_HEADER+BODY+b"/* ignored */\n"+END, DATA.replace(b"COPY public.items", b"SELECT 1;\nCOPY public.items"), b"x"*(16*1024**2+1)):
            self.rejected(ds.screen_data_sql, raw, self.data_policy)

    def test_data_empty_scope_and_limits(self):
        self.assertEqual(ds.screen_data_sql(DATA_HEADER+END, ds.DataPolicy((), ())).table_count, 0)
        raw = DATA_HEADER+b"COPY public.one (value) FROM stdin;\n"+b"x\n"*100001+b"\\.\n"+END
        self.rejected(ds.screen_data_sql, raw, ds.DataPolicy((("public.one", ("value",)),), ()))
        self.rejected(ds.DataPolicy, tuple((f"public.t{i}", ("id",)) for i in range(1001)), ())
        self.rejected(ds.DataPolicy, (("public.t", tuple(f"c{i}" for i in range(513))),), ())

    def test_schema_exact_size_and_hash_only(self):
        raw = b"-- opaque schema\n"
        digest = hashlib.sha256(raw).hexdigest()
        proof = ds.verify_schema_bytes(raw, len(raw), digest)
        self.assertEqual((proof.size, proof.sha256), (len(raw), digest))
        for args in ((raw, True, digest), (raw, len(raw)+1, digest), (raw, len(raw), "0"*64), (raw, len(raw), digest.upper()), (b"", 0, hashlib.sha256(b"").hexdigest()), (b"x"*(8*1024**2+1), 8*1024**2+1, digest)):
            self.rejected(ds.verify_schema_bytes, *args)

    def test_root_frozen_toc_golden(self):
        folder = Path(__file__).resolve().parent/"fixtures/media_backup_b3/dump_screen"
        policy = json.loads((folder/"public016-toc-policy.synthetic.json").read_text())
        raw = (folder/"public016-central-toc.synthetic.txt").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), policy["toc_sha256"])
        result = ds.parse_toc(raw, ds.TocPolicy(tuple(tuple(r) for r in policy["logical_records"])))
        self.assertEqual((result.entry_count, result.header_entries), (798, 802))

    def test_comments_cannot_hide_carriage_return_sql(self):
        for comment in (b"-- comment\rSELECT pg_sleep(1);\n", b"-- comment\r\\connect other\n"):
            self.rejected(ds.screen_data_sql, DATA.replace(b"COPY public.items", comment+b"COPY public.items"), self.data_policy)

    def test_root_frozen_data_golden(self):
        folder = Path(__file__).resolve().parent/"fixtures/media_backup_b3/dump_screen"
        policy_raw = (folder/"public016-data-policy.synthetic.json").read_bytes()
        self.assertEqual(hashlib.sha256(policy_raw).hexdigest(), "8a3387067bbf5e6dd52362441754495b6aecfaad57fd1465152d7e4045107399")
        policy = json.loads(policy_raw)
        trusted = ds.DataPolicy(tuple(("public."+name, tuple(columns)) for name, columns in policy["tables"]), tuple(("public."+name, value, called) for name, value, called in policy["sequences"]))
        raw = (folder/"public016-central-data.synthetic.sql").read_bytes()
        result = ds.screen_data_sql(raw, trusted)
        self.assertEqual((result.table_count, result.sequence_count, result.row_count), (75, 3, 17))

    def test_additional_closed_data_boundaries(self):
        sequence = b"SELECT pg_catalog.setval('public.items_id_seq', -7, true);\n"
        for raw in (DATA.replace(sequence, sequence*2), DATA.replace(sequence, sequence+b"COPY public.empty (id) FROM stdin;\n\\.\n"), DATA.replace(END, b"\\unrestrict wrong_key\n"), DATA_HEADER+b"COPY public.items (id, payload) FROM stdin;\nunterminated\n"+END, DATA.replace(b"SET lock_timeout = 0;", b"SET lock_timeout = 0;\n\\echo hidden")):
            self.rejected(ds.screen_data_sql, raw, self.data_policy)
        for value in (-2**63, 0, 2**63-1):
            raw = DATA_HEADER+f"SELECT pg_catalog.setval('public.s', {value}, false);\n".encode()+END
            self.assertEqual(ds.screen_data_sql(raw, ds.DataPolicy((), (("public.s", value, False),))).sequence_count, 1)
        for value in (None, True, "data", bytearray(DATA)):
            self.rejected(ds.screen_data_sql, value, self.data_policy)
            self.rejected(ds.parse_toc, value, self.toc_policy)

    def test_result_constructors_validate_inner_tuples(self):
        digest = hashlib.sha256(b"x").hexdigest()
        self.rejected(ds.TocInspection, 1, digest, 1, 7, ([1, 0, 0, *RECORDS[0]],))
        self.rejected(ds.DataScreen, 1, digest, 1, 0, 0, (("public.items", ["id"], 0, 0, 1),))
        self.rejected(ds.SchemaHashProof, True, digest)

    def test_unrestrict_is_final_content_and_cannot_occur_before_headers(self):
        for raw in (DATA+b"-- trailing content\n", DATA_HEADER.split(b"SET lock_timeout")[0]+END,
                    DATA_HEADER+b"\\restrict easelB2SchemaComparison20261030\n"+BODY+END,
                    DATA.replace(b"SET row_security = off;", b"SET row_security = off;\nSELECT pg_catalog.set_config('search_path', '', false);")):
            self.rejected(ds.screen_data_sql, raw, self.data_policy)

    def test_limits_at_boundary_and_closed_toc_categories(self):
        # A full valid row allowance, not just a too-large byte buffer.
        policy = ds.DataPolicy((("public.one", ("value",)),), ())
        raw = DATA_HEADER+b"COPY public.one (value) FROM stdin;\n"+b"x\n"*100000+b"\\.\n"+END
        self.assertEqual(ds.screen_data_sql(raw, policy).row_count, 100000)
        columns = tuple(f"c{i}" for i in range(512))
        wide = DATA_HEADER+b"COPY public.wide ("+", ".join(columns).encode()+b") FROM stdin;\n"+b"\t"*511+b"\n\\.\n"+END
        self.assertEqual(ds.screen_data_sql(wide, ds.DataPolicy((("public.wide", columns),), ())).row_count, 1)
        for kind in ("DATABASE", "EXTENSION", "EVENT TRIGGER", "FOREIGN TABLE", "SUBSCRIPTION", "UNKNOWN"):
            self.rejected(ds.TocPolicy, ((kind, "public", "items", "fixture_role"),))
        for fields in (("TABLE", "public", "x\nprivate", "fixture_role"), ("TABLE", "public", "x", "bad owner"), ("TABLE", "public", "x", "非ASCII"), ("TABLE", "private", "x", "fixture_role")):
            self.rejected(ds.TocPolicy, (fields,))
        self.rejected(ds.TocPolicy, RECORDS*3334)
        for raw in (TOC.replace(b"5; 2615", b"True; 2615"), TOC.replace(b"5; 2615", b"5.0; 2615"), TOC.replace(b"1255 77", b"1255.0 77")):
            self.rejected(ds.parse_toc, raw, self.toc_policy)

if __name__ == "__main__": unittest.main(verbosity=2)
