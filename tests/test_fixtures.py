"""夹具级测试：定位精度、误报率、漏报率、字符串/注释零误报。"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from common import FIXTURES_DIR, evaluate  # noqa: E402


class LocationAccuracyTests(unittest.TestCase):
    def test_every_expected_finding_is_hit_exactly(self):
        ev = evaluate()
        if ev.false_negatives:
            missing = "\n".join(str(x) for x in ev.false_negatives)
            self.fail(f"以下期望报警未被精确命中 (规则/行/结构):\n{missing}")
        if ev.false_positives:
            extra = "\n".join(str(f) for f in ev.false_positives)
            self.fail(f"出现了清单之外的报警 (误报):\n{extra}")
        # 定位精度必须 100% (文件/行/结构三元组)
        self.assertEqual(ev.location_accuracy, 1.0)


class PrecisionRecallTests(unittest.TestCase):
    def test_perfect_precision_and_recall_on_labeled_corpus(self):
        ev = evaluate()
        self.assertGreater(len(ev.expected), 0)
        self.assertEqual(ev.precision, 1.0)
        self.assertEqual(ev.recall, 1.0)
        self.assertEqual(len(ev.false_positives), 0)
        self.assertEqual(len(ev.false_negatives), 0)


class StringCommentTests(unittest.TestCase):
    """字符串与注释里的假问题必须零报警。"""

    def test_negative_files_have_zero_findings(self):
        ev = evaluate()
        for finding in ev.actual:
            rel = os.path.relpath(finding.path, FIXTURES_DIR)
            self.assertFalse(
                rel.startswith("negative"),
                f"negative/ 夹具不允许任何报警:\n{finding}",
            )

    def test_specific_string_comment_sample(self):
        n1 = os.path.join(FIXTURES_DIR, "negative", "n1_strings_and_comments.py")
        from revcheck import check_project

        self.assertEqual(check_project(os.path.dirname(n1)), [])


if __name__ == "__main__":
    unittest.main()
