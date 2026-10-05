"""Self-tests: prove each compatibility scenario yields the expected verdict,
and that incompatibilities are located to the exact field."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rollback_verify import codec
from rollback_verify import reader as R
from rollback_verify.checker import (CompatibilityChecker, verdict_and_steps,
                                     VERDICT_OK, VERDICT_CONVERT,
                                     VERDICT_NO_ROLLBACK)
import scenarios as S


def verify_all(new_schema, mode, mappings, count=500):
    checker = CompatibilityChecker(S.OLD, new_schema, enum_mappings=mappings)
    records = list(S.gen_records(seed=1, count=count, mode=mode))
    data = codec.encode_data_file(records, new_schema.data_format_version)
    _, decoded, fmt_issues = checker.reader.read_data_file(data)
    issues = list(fmt_issues)
    issues += checker.check_index_files(S.new_index_files(new_schema, records))
    issues += checker.check_contract()
    ok = 0
    per_record_bad = 0
    for seq, rec in enumerate(decoded):
        ri = [i for i in checker.check_record(rec, seq)
              if i.kind != R.KIND_UNKNOWN_FIELD]
        issues += ri
        if ri:
            per_record_bad += 1
        else:
            ok += 1
    verdict, steps = verdict_and_steps(issues)
    return verdict, steps, issues, ok, per_record_bad


class TestRollbackScenarios(unittest.TestCase):

    def test_fully_compatible(self):
        verdict, _, issues, ok, bad = verify_all(S.SCHEMA_COMPAT, "compat", None)
        self.assertEqual(verdict, VERDICT_OK)
        self.assertEqual(ok, 500)
        self.assertEqual(bad, 0)
        self.assertEqual(issues, [])

    def test_additive_fields_are_ignored_by_old(self):
        verdict, _, issues, ok, bad = verify_all(S.SCHEMA_ADDITIVE, "additive", None)
        self.assertEqual(verdict, VERDICT_OK)
        self.assertEqual(ok, 500)
        self.assertEqual(bad, 0)
        self.assertEqual(issues, [])

        reader = R.OldVersionReader(S.OLD)
        rec = {"id": 1, "name": "a", "status": "ACTIVE",
               "email": "a@b.com", "remark": "x"}
        flagged = reader.read_record(rec)
        self.assertTrue(all(i.kind == R.KIND_UNKNOWN_FIELD for i in flagged))
        self.assertEqual({i.field for i in flagged}, {"email", "remark"})

    def test_new_enum_value_detected_on_status_field(self):
        verdict, _, issues, ok, bad = verify_all(
            S.SCHEMA_NEW_ENUM, "new_enum", S.ENUM_MAPPINGS)
        self.assertEqual(verdict, VERDICT_CONVERT)
        self.assertEqual(bad, 50)
        enum_issues = [i for i in issues if i.kind == R.KIND_ENUM_UNKNOWN]
        self.assertEqual(len(enum_issues), 50)
        self.assertTrue(all(i.field == "status" for i in enum_issues))
        self.assertTrue(all(i.convertible for i in enum_issues))
        self.assertTrue(any("PENDING_DELETE" in i.conversion for i in enum_issues))

    def test_unknown_enum_without_mapping_blocks_rollback(self):
        verdict, _, _, _, _ = verify_all(S.SCHEMA_NEW_ENUM, "new_enum", None)
        self.assertEqual(verdict, VERDICT_NO_ROLLBACK)

    def test_index_structure_change_requires_rebuild(self):
        verdict, _, issues, _, _ = verify_all(
            S.SCHEMA_INDEX_CHANGE, "index_change", S.ENUM_MAPPINGS)
        self.assertEqual(verdict, VERDICT_CONVERT)
        fmt = [i for i in issues if i.kind == R.KIND_INDEX_FORMAT]
        layout = [i for i in issues if i.kind == R.KIND_INDEX_LAYOUT]
        self.assertEqual({i.field for i in fmt},
                         {"indexes.idx_status.format_version"})
        self.assertEqual({i.field for i in layout},
                         {"indexes.idx_status.keys"})
        self.assertTrue(all(i.convertible for i in fmt + layout))

    def test_destructive_change_not_rollbackable(self):
        verdict, steps, issues, ok, bad = verify_all(
            S.SCHEMA_DESTRUCTIVE, "destructive", None)
        self.assertEqual(verdict, VERDICT_NO_ROLLBACK)
        self.assertEqual(ok, 0)
        self.assertEqual(bad, 500)
        missing = [i for i in issues if i.kind == R.KIND_MISSING_REQUIRED]
        self.assertEqual({i.field for i in missing}, {"name"})
        self.assertTrue(any(not i.convertible for i in missing))
        removed_op = [i for i in issues if i.kind == R.KIND_CONTRACT_OP_MISSING]
        self.assertEqual({i.field for i in removed_op}, {"contract.get_user"})
        self.assertTrue(any("备份" in s for s in steps))

    def test_type_mismatch_blocks_rollback(self):
        reader = R.OldVersionReader(S.OLD)
        issues = reader.read_record(
            {"id": "not-an-int", "name": "x", "status": "ACTIVE"})
        self.assertEqual(issues[0].kind, R.KIND_TYPE_MISMATCH)
        self.assertEqual(issues[0].field, "id")

    def test_data_format_header(self):
        data = codec.encode_data_file(
            [{"id": 1, "name": "a", "status": "ACTIVE"}], data_format_version=2)
        _, _, issues = R.OldVersionReader(S.OLD).read_data_file(data)
        self.assertEqual(issues[0].kind, R.KIND_FORMAT_VERSION)


if __name__ == "__main__":
    unittest.main(verbosity=2)
