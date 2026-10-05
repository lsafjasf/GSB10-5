import json
import unittest

from citation_renumber import (
    CitationError,
    analyze_document,
    renumber_document,
)


class CitationRenumberTests(unittest.TestCase):
    def test_single_citation_is_numbered_one(self):
        document = "正文[4]。\n\n## 参考文献\n\n[4] 第四条\n"

        result = renumber_document(document)

        self.assertEqual(result.analysis.mapping, {4: 1})
        self.assertIn("正文[1]。", result.text)
        self.assertIn("[1] 第四条", result.text)
        self.assertTrue(
            result.consistency["body_content_byte_comparison"]["equal"]
        )
        self.assertTrue(result.consistency["citation_sequence"]["equal"])

    def test_repeated_citation_reuses_one_number(self):
        document = "第一次[3]，第二次[3]，第三次[3]。\n\n## 参考文献\n\n[3] 第三条\n"

        result = renumber_document(document)

        self.assertEqual(result.analysis.mapping, {3: 1})
        self.assertEqual(result.text.count("[1]"), 4)  # 正文 3 次 + 文末 1 次
        self.assertEqual(result.analysis.diagnostics()["unique_cited_entries"], 1)

    def test_first_appearance_order_controls_mapping(self):
        document = "乙[8]，甲[2]，乙再次[8]。\n\n## 参考文献\n\n[2] 甲\n[8] 乙\n"

        result = renumber_document(document)

        self.assertEqual(result.analysis.mapping, {8: 1, 2: 2})
        self.assertIn("乙[1]，甲[2]，乙再次[1]。", result.text)
        self.assertLess(result.text.index("[1] 乙"), result.text.index("[2] 甲"))

    def test_orphan_entries_are_reported_and_moved_after_cited_entries(self):
        document = "正文[3]。\n\n## 参考文献\n\n[1] 孤立甲\n[3] 被引用\n[5] 孤立乙\n"

        result = renumber_document(document)

        self.assertEqual(result.analysis.orphan_entries, (1, 5))
        self.assertEqual(result.analysis.mapping, {3: 1, 1: 2, 5: 3})
        self.assertIn("[1] 被引用", result.text)
        self.assertIn("[2] 孤立甲", result.text)
        self.assertIn("[3] 孤立乙", result.text)
        self.assertEqual(
            result.consistency["reference_list"]["orphan_entries"], [2, 3]
        )

    def test_numbering_gaps_are_reported(self):
        document = "正文[1][3][7]。\n\n## 参考文献\n\n[1] 一\n[3] 三\n[7] 七\n"

        analysis = analyze_document(document)

        self.assertEqual(analysis.numbering_gaps, (2, 4, 5, 6))
        self.assertEqual(analysis.mapping, {1: 1, 3: 2, 7: 3})

    def test_missing_entry_is_classified_and_blocks_rewrite_by_default(self):
        document = "正文[1][9]。\n\n## 参考文献\n\n[1] 一\n"

        analysis = analyze_document(document)
        self.assertEqual(analysis.missing_entries, (9,))
        self.assertEqual(analysis.orphan_entries, ())

        with self.assertRaises(CitationError) as raised:
            renumber_document(document)
        self.assertIn("不存在的条目", str(raised.exception))
        self.assertEqual(raised.exception.analysis.missing_entries, (9,))

    def test_missing_entry_can_be_left_unchanged_in_diagnostic_mode(self):
        document = "正文[2][9]。\n\n## 参考文献\n\n[2] 二\n"

        result = renumber_document(document, allow_missing=True)

        self.assertIn("正文[1][9]。", result.text)
        self.assertFalse(
            result.consistency["reference_list"]["all_body_citations_resolve"]
        )

    def test_body_non_citation_bytes_are_identical_after_rewrite(self):
        document = "甲[3]。\n乙[1]，甲再次[3]。\n\n## 参考文献\n\n[1] 一\n[3] 三\n"

        result = renumber_document(document)
        comparison = result.consistency["body_content_byte_comparison"]

        self.assertTrue(comparison["equal"])
        self.assertIsNone(comparison["first_difference_byte"])
        self.assertEqual(
            comparison["original_sha256"], comparison["rewritten_sha256"]
        )
        self.assertFalse(result.consistency["raw_body_bytes"]["equal"])

    def test_report_is_json_serializable(self):
        document = "正文[2]。\n\n## 参考文献\n\n[2] 二\n"

        payload = json.dumps(renumber_document(document).report(), ensure_ascii=False)

        self.assertIn('"old": 2', payload)
        self.assertIn('"new": 1', payload)


if __name__ == "__main__":
    unittest.main()
