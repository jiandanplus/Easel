import copy
import dataclasses
import json
import unittest
from test_media_backup_b3_contract import fixture
from platform_service.media_backup_b3.contracts import B3Error
from platform_service.media_backup_b3.compare import normalize_observation, compare_observations


class CompareTests(unittest.TestCase):
    def test_only_three_paths_and_nonmutation(self):
        source, sc = fixture(); target, tc = fixture("target_db")
        target["roles"]["memberships"][0][6] = "999"
        target["roles"]["credentials"][0][2] = "1000"
        target["roles"]["credentials"][1][2] = "1001"
        before = json.dumps([source, target]).encode()
        proof = compare_observations(source, target, sc, tc)
        self.assertTrue(proof.equal)
        self.assertNotEqual(proof.source_sha256, proof.target_sha256)
        self.assertEqual(proof.source_normalized_sha256, proof.target_normalized_sha256)
        self.assertEqual(before, json.dumps([source, target]).encode())
        expected = copy.deepcopy(source)
        expected["schema"]["database"][0][0] = "<trusted-database>"
        expected["roles"]["memberships"][0][6] = "<membership-xmin>"
        for r in expected["roles"]["credentials"]: r[2] = "<credential-xmin>"
        self.assertEqual(normalize_observation(source, sc), expected)
        self.assertTrue(all(type(v) in (bool, str) for v in dataclasses.asdict(proof).values()))

    def assert_change_detected(self, source, context, target):
        try:
            proof = compare_observations(source, target, context, context)
        except B3Error:
            return
        self.assertFalse(proof.equal)

    def test_every_nonexempt_catalog_position(self):
        source, ctx = fixture()
        for name, rows in source["schema"].items():
            for ri, row in enumerate(rows):
                for ci, value in enumerate(row):
                    if name == "database" and ci == 0: continue
                    target = copy.deepcopy(source)
                    replacement = (not value if type(value) is bool else value + 1 if type(value) is int
                                   else value + "_changed" if type(value) is str
                                   else value + ["changed"] if type(value) is list else "changed")
                    target["schema"][name][ri][ci] = replacement
                    with self.subTest(catalog=name, row=ri, column=ci):
                        self.assert_change_detected(source, ctx, target)

    def test_every_nonexempt_role_position(self):
        source, ctx = fixture()
        for name, rows in source["roles"].items():
            for ri, row in enumerate(rows):
                for ci, value in enumerate(row):
                    if (name, ci) in {("memberships", 6), ("credentials", 2)}: continue
                    target = copy.deepcopy(source)
                    target["roles"][name][ri][ci] = (not value if type(value) is bool else value+1 if type(value) is int
                                                    else value+"_changed" if type(value) is str else "changed")
                    with self.subTest(role=name, row=ri, column=ci):
                        self.assert_change_detected(source, ctx, target)

    def test_sequences_rows_copy_never_normalized(self):
        source, ctx = fixture()
        for key, value in source["sequences"][0].items():
            target = copy.deepcopy(source)
            target["sequences"][0][key] = (not value if type(value) is bool else value+1 if type(value) is int else value+"_changed")
            with self.subTest(sequence=key): self.assert_change_detected(source, ctx, target)
        target = copy.deepcopy(source); target["rows"]["items"] = []; target["copy"]["public.items"]["count"] = 0
        self.assert_change_detected(source, ctx, target)
        target = copy.deepcopy(source); target["copy"]["public.items"]["rows_sha256"] = "b"*64
        self.assert_change_detected(source, ctx, target)

    def test_context_inventory_mismatch_fails_before_normalization(self):
        source, ctx = fixture()
        for changed in [dataclasses.replace(ctx, role_names=("app",)),
                        dataclasses.replace(ctx, allowed_grantors=("other",)),
                        dataclasses.replace(ctx, row_order_pins=(("items", ("b"*64,)),))]:
            with self.assertRaises(B3Error): compare_observations(source, source, ctx, changed)
