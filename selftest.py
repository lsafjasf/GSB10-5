#!/usr/bin/env python3
"""分层缓存分析自测（python3 selftest.py -v）。

覆盖：
  1. 完全相同的两组脚本                 → 100% 复用
  2. 仅指令顺序调整                     → 首个分叉点起全部重建
  3. 中间一层变化                       → 前缀复用、后缀全部重建
  4. 仅时间戳与属主变化                 → 100% 复用
  5. 内容/权限/符号链接变化（负例）      → 必须失效，且不错误复用
  6. 缓存损坏防护                       → 内容不一致时校验失败
  7. touch 仅改 mtime                  → 层内容摘要不变、缓存命中
  8. 缓存键稳定性与链式失效单测
  9. 1000 组脚本基准冒烟检查
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from layercache.analysis import analyze_pair
from layercache.builder import (
    Artifact, CacheStore, changeset_digest, run_build,
)
from layercache.keys import chain_key, entry_descriptor, file_tree_digest, instruction_factor
from layercache.model import BuildScript, FileRecord, Instruction
from layercache.parser import parse_script
from layercache.scenarios import negative_pairs, scenario_pairs
from layercache.verify import verify_pair
from layercache.bench import run_benchmark


class ScenarioTests(unittest.TestCase):
    def test_identical(self):
        name, base, candidate = scenario_pairs()[0]
        report = analyze_pair(base, candidate, CacheStore(), name=name)
        self.assertEqual(report.total_layers, 7)
        self.assertEqual(report.reused_layers, 7)
        self.assertEqual(report.rebuilt_layers, 0)
        self.assertAlmostEqual(report.layer_reuse_rate, 1.0)
        self.assertIsNone(report.first_divergence_index)
        self.assertTrue(report.verified)
        # 完全相同 => 产物镜像摘要相同
        self.assertTrue(report.verification.final_image_match)

    def test_reordered(self):
        name, base, candidate = scenario_pairs()[1]
        report = analyze_pair(base, candidate, CacheStore(), name=name)
        # 层 0/1 相同；层 2 起 COPY 交换 -> 全部重建
        self.assertEqual(report.first_divergence_index, 2)
        self.assertEqual(report.reused_layers, 2)
        self.assertEqual(report.rebuilt_layers, 5)
        self.assertAlmostEqual(report.layer_reuse_rate, 2 / 7)
        # 指令集合完全重合，但链式缓存仍失效（说明顺序敏感）
        self.assertAlmostEqual(report.instruction_overlap, 1.0)
        self.assertTrue(report.verified)
        self.assertTrue(
            all(v.verdict == "reuse" for v in report.verdicts[:2]))
        self.assertTrue(
            all(v.verdict == "rebuild" for v in report.verdicts[2:]))

    def test_mid_change(self):
        name, base, candidate = scenario_pairs()[2]
        report = analyze_pair(base, candidate, CacheStore(), name=name)
        # 变化点是层 4 的 RUN
        self.assertEqual(report.first_divergence_index, 4)
        self.assertEqual(report.reused_layers, 4)
        self.assertEqual(report.rebuilt_layers, 3)
        self.assertAlmostEqual(report.layer_reuse_rate, 4 / 7)
        self.assertTrue(report.verified)
        # 变化层之后的 ENV(5)/WORKDIR(6) 即使文本相同也必须重建
        self.assertEqual(
            [v.verdict for v in report.verdicts],
            ["reuse"] * 4 + ["rebuild"] * 3)

    def test_metadata_only(self):
        name, base, candidate = scenario_pairs()[3]
        report = analyze_pair(base, candidate, CacheStore(), name=name)
        # mtime/uid/gid 全部改变，但内容/权限未变 -> 100% 复用
        self.assertEqual(report.reused_layers, 7)
        self.assertAlmostEqual(report.layer_reuse_rate, 1.0)
        self.assertTrue(report.verified)
        # 且最终镜像内容摘要一致（uid/gid/mtime 不进摘要）
        self.assertTrue(report.verification.final_image_match)


class NegativeTests(unittest.TestCase):
    def test_content_and_mode_invalidation(self):
        for name, base, candidate in negative_pairs():
            report = analyze_pair(base, candidate, CacheStore(), name=name)
            # 首个 COPY 在层 2，内容或权限变化 -> 从层 2 起全部重建
            self.assertEqual(report.first_divergence_index, 2, msg=name)
            self.assertEqual(report.reused_layers, 2, msg=name)
            self.assertTrue(report.verified, msg=name)

    def test_symlink_target_invalidation(self):
        ctx_a = (FileRecord("/src/l", kind="symlink", target="/a", mode=0o777),)
        ctx_b = (FileRecord("/src/l", kind="symlink", target="/b", mode=0o777),)
        ins = (Instruction("FROM", "ubuntu:22.04"),
               Instruction("COPY", "/src/l /tmp/l"))
        report = analyze_pair(BuildScript("a", ins, ctx_a),
                              BuildScript("b", ins, ctx_b), CacheStore())
        self.assertEqual(report.reused_layers, 1)
        self.assertEqual(report.first_divergence_index, 1)
        self.assertTrue(report.verified)

    def test_ignoring_metadata_does_not_cause_wrong_reuse(self):
        # 仅元数据不同的两次"无缓存重建"，层内容摘要必须相同；
        # 而内容不同时摘要必须不同（双向往复，防止错误复用的另一面）。
        r1 = FileRecord("/f", kind="file", content=b"x", mode=0o644,
                        mtime=1, uid=1, gid=1)
        r2 = FileRecord("/f", kind="file", content=b"x", mode=0o644,
                        mtime=999, uid=999, gid=999)
        r3 = FileRecord("/f", kind="file", content=b"y", mode=0o644,
                        mtime=999, uid=999, gid=999)
        self.assertEqual(file_tree_digest([r1]), file_tree_digest([r2]))
        self.assertNotEqual(file_tree_digest([r2]), file_tree_digest([r3]))

    def test_mode_change_changes_descriptor(self):
        r1 = FileRecord("/f", kind="file", content=b"x", mode=0o644)
        r2 = FileRecord("/f", kind="file", content=b"x", mode=0o755)
        self.assertNotEqual(entry_descriptor(r1), entry_descriptor(r2))


class TouchTests(unittest.TestCase):
    SCRIPT = (
        "FROM ubuntu:22.04\n"
        "RUN write /data.txt hello\n"
        "RUN touch /data.txt\n"
        "RUN touch /data.txt\n"
    )

    def test_touch_is_content_neutral(self):
        # touch 改变 mtime，但层内容摘要排除 mtime：
        # 同一次构建中两个 touch 层的规范化内容摘要相同；
        # 再次构建同一脚本时，所有层（含 touch）都应命中缓存。
        cache = CacheStore()
        first = run_build(parse_script(self.SCRIPT, "touch"), cache)
        self.assertEqual(len(first.layers), 4)
        self.assertEqual(first.layers[2].content_digest,
                         first.layers[3].content_digest)
        # 变化集如实记录了 mtime 的变化（没有因为忽略而丢失事实）
        self.assertNotEqual(first.layers[2].changes[0].mtime,
                            first.layers[3].changes[0].mtime)
        second = run_build(parse_script(self.SCRIPT, "touch"), cache)
        self.assertTrue(all(layer.reused for layer in second.layers))


class TamperTests(unittest.TestCase):
    def test_corrupt_cache_detected(self):
        script = parse_script(
            "FROM ubuntu:22.04\nRUN write /a x\n", "tamper")
        cache = CacheStore()
        cached = run_build(script, cache)
        target_key = cached.layers[1].cache_key
        cache.corrupt(target_key)
        tampered = run_build(script, cache)
        rebuilt = run_build(script, None)
        verification = verify_pair(tampered, rebuilt, name="tamper")
        self.assertFalse(verification.ok)
        self.assertTrue(tampered.layers[1].reused)
        self.assertNotEqual(
            verification.checks[1].content_digest_cached,
            verification.checks[1].content_digest_rebuilt)


class CacheKeyTests(unittest.TestCase):
    def test_key_is_stable_and_parent_sensitive(self):
        f1 = instruction_factor(Instruction("RUN", "write /a 1"))
        f2 = instruction_factor(Instruction("RUN", "write /a 2"))
        key_a = chain_key(None, f1)
        key_b1 = chain_key(key_a, f2)
        key_b2 = chain_key(key_a, f2)
        self.assertEqual(key_b1, key_b2)                 # 确定性
        self.assertNotEqual(chain_key(key_a, f2),
                            chain_key(chain_key(None, f1 + b"x"), f2))
        self.assertTrue(key_b1.startswith("layercache:"))
        self.assertEqual(len(key_b1), len("layercache:") + 64)

    def test_metadata_not_in_factor(self):
        ctx = (FileRecord("/a", kind="file", content=b"x", mode=0o644,
                          mtime=1, uid=1, gid=1),)
        ctx2 = (FileRecord("/a", kind="file", content=b"x", mode=0o644,
                           mtime=2, uid=3, gid=4),)
        instr = Instruction("COPY", "/a /a")
        self.assertEqual(instruction_factor(instr, ctx),
                         instruction_factor(instr, ctx2))

    def test_content_dest_in_factor(self):
        ctx = (FileRecord("/a", kind="file", content=b"x", mode=0o644),)
        self.assertNotEqual(
            instruction_factor(Instruction("COPY", "/a /a"), ctx),
            instruction_factor(Instruction("COPY", "/a /b"), ctx))
        ctx2 = (FileRecord("/a", kind="file", content=b"y", mode=0o644),)
        self.assertNotEqual(
            instruction_factor(Instruction("COPY", "/a /a"), ctx),
            instruction_factor(Instruction("COPY", "/a /a"), ctx2))


class ParserTests(unittest.TestCase):
    def test_parse_context_marker_and_whitespace(self):
        text = (
            "FROM ubuntu:22.04\n"
            "RUN   echo   hi\n"
            "# comment\n\n"
            "--- CONTEXT ---\n"
            '{"files": [{"path": "a.txt", "content": "abc", "mode": 493}]}')
        script = parse_script(text, "p")
        self.assertEqual(script.instructions[1].arg, "echo hi")
        self.assertEqual(script.context[0].path, "/a.txt")
        self.assertEqual(script.context[0].mode, 0o755)
        self.assertEqual(script.context[0].content, b"abc")


class BenchmarkSmokeTest(unittest.TestCase):
    def test_benchmark_runs_and_has_hits(self):
        result = run_benchmark(1000)
        self.assertGreater(result.hits, 0)
        self.assertGreater(result.misses, 0)
        self.assertGreater(result.peak_traced_bytes, 0)
        self.assertGreater(result.wall_seconds, 0)
        self.assertEqual(result.script_count, 1000)
        # 偶数脚本应整体命中
        self.assertGreaterEqual(result.hit_rate, 0.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
