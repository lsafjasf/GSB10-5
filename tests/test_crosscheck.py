"""库实现 vs 独立参考实现对拍，覆盖格式×字段集合的组合空间。"""

import itertools
import unittest

from export_negotiation import FORMATS, ALIASES, FALLBACK_FAMILY
from export_negotiation.negotiator import negotiate as lib_negotiate

from tests.reference_oracle import (
    ORACLE_FORMATS,
    ORACLE_ALIASES,
    ORACLE_FALLBACK,
    negotiate as oracle_negotiate,
)

REQUEST_FORMATS = (
    list(FORMATS)
    + list(ALIASES)
    + list(FALLBACK_FAMILY)
    + ["yaml", "avro", "parquet", "pdf"]
)

FIELD_TYPES = ["str", "int", "float", "bool", "date", "datetime",
               "object", "array", "binary"]

FIELDS_CORPUS = []
for size in (1, 2, 3, 5):
    for combo in itertools.product(FIELD_TYPES, repeat=size):
        # 全组合太大，用确定性抽样：只取有序组合（可重复），规模 C(n+size-1, size)
        if list(combo) != sorted(combo):
            continue
        FIELDS_CORPUS.append([("f%d" % i, t) for i, t in enumerate(combo)])


def lib_conclusion(fmt, fields):
    r = lib_negotiate(fmt, fields)
    return (
        r["status"],
        r["format"],
        tuple(r["exportable_fields"]),
        tuple(m["name"] for m in r["missing_fields"]),
    )


class DataConsistencyTests(unittest.TestCase):
    def test_oracle_data_matches_registry(self):
        self.assertEqual(set(ORACLE_FORMATS), set(FORMATS))
        self.assertEqual(ORACLE_ALIASES, dict(ALIASES))
        self.assertEqual(ORACLE_FALLBACK, dict(FALLBACK_FAMILY))
        for fid, (pref, fam, types) in ORACLE_FORMATS.items():
            self.assertEqual(FORMATS[fid]["preference"], pref)
            self.assertEqual(FORMATS[fid]["family"], fam)
            self.assertEqual(set(FORMATS[fid]["field_types"]), types)


class CrossCheckTests(unittest.TestCase):
    def test_all_combinations_match_oracle(self):
        total = 0
        for fmt in REQUEST_FORMATS:
            for fields in FIELDS_CORPUS:
                o = oracle_negotiate(fmt, fields)
                expected = (o[0], o[1], tuple(o[2]), tuple(o[3]))
                actual = lib_conclusion(fmt, fields)
                self.assertEqual(actual, expected,
                                 "分歧: fmt=%s fields=%s" % (fmt, fields))
                total += 1
        # 对拍样本量下限，防止 corpus 被意外削弱
        self.assertGreaterEqual(total, 1000)


if __name__ == "__main__":
    unittest.main()
