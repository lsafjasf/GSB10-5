#!/usr/bin/env python3
"""docdiff 自测（仅标准库 unittest）。运行: python3 run_tests.py [-v]"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from docdiff import (diff_documents, expand_summary, summarize,
                     verify_roundtrip)
from docdiff.model import parse_document

EX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples")


def load(name):
    with open(os.path.join(EX, name), encoding="utf-8") as f:
        return f.read()


def by_old_title(diff, title):
    return [c for c in diff.changes if c.old_title == title]


def by_new_title(diff, title):
    return [c for c in diff.changes if c.new_title == title]


class TestParse(unittest.TestCase):
    def test_headings_and_anchor(self):
        idx = parse_document("# A {#x}\n\nbody\n\n## B\n\nb2\n")
        self.assertEqual(len(idx.sections), 3)  # root + A + B
        a = idx.sections[1]
        self.assertEqual((a.title, a.anchor, a.level), ("A", "x", 1))
        self.assertEqual(idx.sections[2].parent.id, a.id)

    def test_fence_hash_not_heading(self):
        idx = parse_document("# A\n\n```\n# not a heading\n```\n")
        self.assertEqual(len(idx.sections), 2)
        self.assertIn("# not a heading", idx.sections[1].body_lines)

    def test_level_jump(self):
        idx = parse_document("# A\n\n### deep\n")
        deep = idx.sections[2]
        self.assertEqual(deep.parent.id, idx.sections[1].id)


class TestScenarios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.diff = diff_documents(load("old.md"), load("new.md"))

    def test_identical(self):
        d = diff_documents(load("old.md"), load("old.md"))
        self.assertEqual(d.stats(), {"unchanged": d.old_section_count})
        self.assertEqual(d.matched_pairs, d.old_section_count)

    def test_rename_only(self):
        (c,) = by_old_title(self.diff, "快速开始")
        self.assertEqual(c.category, "rename")
        self.assertEqual(c.new_title, "快速上手")
        self.assertIn("renamed", c.flags)
        self.assertNotIn("moved", c.flags)
        self.assertEqual(c.old_parent_path, c.new_parent_path)

    def test_whole_chapter_move(self):
        (c,) = by_old_title(self.diff, "任务")
        self.assertEqual(c.category, "reparent")
        self.assertIn("moved", c.flags)
        self.assertNotIn("renamed", c.flags)
        self.assertEqual(c.old_parent_path, "产品手册 > 核心概念")
        self.assertEqual(c.new_parent_path, "产品手册 > 高级配置")
        self.assertEqual(c.content_jaccard, 1.0)

    def test_move_not_delete_add(self):
        # 移动/改名不得产生对应的 deleted+added 对
        self.assertEqual(by_old_title(self.diff, "任务")[0].category, "reparent")
        self.assertEqual(by_new_title(self.diff, "任务")[0].category, "reparent")
        self.assertEqual(by_old_title(self.diff, "快速开始")[0].category, "rename")
        self.assertEqual(by_new_title(self.diff, "快速上手")[0].category, "rename")
        deleted_titles = [c.old_title for c in self.diff.changes
                          if c.category == "deleted"]
        added_titles = [c.new_title for c in self.diff.changes
                        if c.category == "added"]
        self.assertNotIn("任务", deleted_titles)
        self.assertNotIn("任务", added_titles)
        self.assertNotIn("快速开始", deleted_titles)
        self.assertNotIn("快速上手", added_titles)

    def test_level_change(self):
        # 术语表: 父章节不变(附录->附录), 仅标题级别变化 -> 纯层级调整
        (t,) = by_old_title(self.diff, "术语表")
        self.assertEqual(t.category, "level_change")
        self.assertEqual((t.old_level, t.new_level), (3, 4))
        self.assertIn("level_changed", t.flags)
        self.assertNotIn("moved", t.flags)
        # 附录: H2 提升为 H1, 父由「产品手册」变为 root -> 移动+层级调整复合
        (c,) = by_old_title(self.diff, "附录")
        self.assertEqual(c.category, "reparent")
        self.assertIn("level_changed", c.flags)
        self.assertIn("moved", c.flags)
        self.assertEqual((c.old_level, c.new_level), (2, 1))

    def test_add_delete(self):
        self.assertEqual(by_old_title(self.diff, "已知问题")[0].category, "deleted")
        for t in ("生产部署", "容器化部署", "日志配置"):
            self.assertEqual(by_new_title(self.diff, t)[0].category, "added")

    def test_modified_and_reorder(self):
        (c,) = by_old_title(self.diff, "数据接口")
        self.assertEqual(c.category, "modified")
        self.assertLess(c.content_jaccard, 1.0)
        (r,) = by_old_title(self.diff, "认证接口")
        self.assertEqual(r.category, "unchanged")
        self.assertIn("reordered", r.flags)

    def test_unchanged_sections(self):
        for t in ("安装环境", "项目", "模块"):
            (c,) = by_old_title(self.diff, t)
            self.assertEqual(c.category, "unchanged")
            self.assertEqual(c.flags, [])


class TestContainerInference(unittest.TestCase):
    def test_introless_container_move(self):
        # 「安装」章自身无正文，两个子章节内容充实；整章搬到文末后
        # 父章节应由已配对子节点的多数派推断配对，而不是 deleted+added。
        win = "安装程序首先检查系统版本。\n然后解压运行时组件。\n接着写入注册表项。\n最后创建桌面快捷方式。\n"
        mac = "安装前确认系统完整性保护状态。\n拖动应用图标到应用程序目录。\n首次启动需要授予权限。\n随后检查命令行工具链接。\n"
        old = ("# 平台指南\n\n指南前言内容。\n\n## 安装\n\n### Windows\n\n" + win +
               "\n### macOS\n\n" + mac + "\n# 运维手册\n\n运维前言内容。\n")
        new = ("# 平台指南\n\n指南前言内容。\n\n# 运维手册\n\n运维前言内容。\n" +
               "\n## 安装\n\n### Windows\n\n" + win + "\n### macOS\n\n" + mac)
        d = diff_documents(old, new)
        (c,) = by_old_title(d, "安装")
        self.assertEqual(c.category, "reparent")
        self.assertIn("推断", c.reason)
        self.assertEqual(c.old_parent_path, "平台指南")
        self.assertEqual(c.new_parent_path, "运维手册")
        deleted = [x.old_title for x in d.changes if x.category == "deleted"]
        self.assertNotIn("安装", deleted)


class TestRoundtrip(unittest.TestCase):
    def test_roundtrip_demo(self):
        diff = diff_documents(load("old.md"), load("new.md"))
        res = verify_roundtrip(diff)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["change_count"], res["manifest_count"])
        self.assertEqual(res["manifest_count"], res["expanded_count"])

    def test_roundtrip_identical(self):
        diff = diff_documents(load("old.md"), load("old.md"))
        self.assertTrue(verify_roundtrip(diff)["ok"])

    def test_summary_json_stable(self):
        diff = diff_documents(load("old.md"), load("new.md"))
        s1 = json.dumps(summarize(diff), ensure_ascii=False, sort_keys=True)
        s2 = json.dumps(summarize(diff), ensure_ascii=False, sort_keys=True)
        self.assertEqual(s1, s2)

    def test_expand_skeleton_fields(self):
        diff = diff_documents(load("old.md"), load("new.md"))
        expanded = expand_summary(summarize(diff))
        for orig, skel in zip(diff.changes, expanded):
            self.assertEqual(orig.category, skel.category)
            self.assertEqual(orig.old_path, skel.old_path)
            self.assertEqual(orig.new_path, skel.new_path)
            self.assertEqual(orig.old_title, skel.old_title)
            self.assertEqual(orig.new_title, skel.new_title)


class TestThreshold(unittest.TestCase):
    def test_rename_breaks_at_high_threshold(self):
        # 快速开始->快速上手: 内容相同(1.0), 标题相似度约0.67,
        # score = 0.8 + 0.2*tsim ≈ 0.93; 阈值 0.95 时配对失败 -> deleted+added
        old = "# 安装指南\n\n" + "相同的正文内容。\n" * 8
        new = "# 配置手册\n\n" + "相同的正文内容。\n" * 8
        d_ok = diff_documents(old, new, threshold=0.6)
        self.assertEqual(d_ok.changes[0].category, "rename")
        d_hi = diff_documents(old, new, threshold=0.99)
        cats = sorted(c.category for c in d_hi.changes)
        self.assertEqual(cats, ["added", "deleted"])

    def test_anchor_survives_high_threshold(self):
        old = "# 安装指南 {#g}\n\n" + "旧内容甲。\n" * 8
        new = "# 完全不同的标题 {#g}\n\n" + "新内容乙。\n" * 8
        d = diff_documents(old, new, threshold=0.99)
        self.assertEqual(d.changes[0].category, "rename")
        self.assertIn("锚点", d.changes[0].reason)


if __name__ == "__main__":
    unittest.main(verbosity=2 if "-v" in sys.argv else 1)
