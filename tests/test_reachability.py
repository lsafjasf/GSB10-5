"""可达性分析自测（stdlib unittest）。

覆盖：直接可达、间接可达、确认不可达（未引用/无路径/条件挡死）、
依赖缺失、动态导入、反射、配置驱动、未解析动态分发、路径可逐跳核对。
"""

import unittest

from reachability import Analyzer, Vulnerability, build_graph
from reachability.analyzer import (
    STATUS_DEP_MISSING,
    STATUS_POTENTIAL,
    STATUS_REACHABLE,
    STATUS_UNREACHABLE,
)
from reachability.callgraph import (
    EDGE_CONFIG,
    EDGE_DYNAMIC_IMPORT,
    EDGE_REFLECTION,
    EDGE_STATIC,
)

VULN_SRC = '''
def parse(data):
    return data


def helper():
    return 1
'''

APP_DIRECT = '''
from vulnpkg import parse


def main():
    return parse("x")
'''

APP_INDIRECT = '''
from vulnpkg import parse


def main():
    return run()


def run():
    return parse("x")
'''

APP_NEVER_REF = '''
def main():
    return 42
'''

APP_NO_PATH = '''
from vulnpkg import parse


def main():
    return 1


def dead_handler():
    return parse("x")
'''

APP_BLOCKED = '''
from vulnpkg import parse

ENABLE_LEGACY = False


def main():
    if ENABLE_LEGACY:
        return parse("x")
    return 0
'''

APP_DYN_IMPORT = '''
import importlib


def main():
    mod = importlib.import_module("vulnpkg")
    return mod
'''

VULN_WITH_MAIN_CALL = '''
def parse(data):
    return data


parse("boot")
'''

APP_REFLECTION = '''
import vulnpkg


def main():
    func = getattr(vulnpkg, "parse")
    return func("x")
'''

APP_UNRESOLVED_DYN = '''
import importlib


def main():
    name = input()
    return importlib.import_module(name)
'''


def analyze(app_src, vuln_symbols=("vulnpkg.parse",), vuln_src=VULN_SRC,
            config_strings=(), installed=("vulnpkg",), dep="vulnpkg",
            extra_files=()):
    files = [("app", "app.py", app_src)]
    if vuln_src is not None:
        files.append(("vulnpkg", "vulnpkg.py", vuln_src))
    files.extend(extra_files)
    graph = build_graph(files, config_strings=config_strings)
    vulns = [Vulnerability("V-1", dep, tuple(vuln_symbols))]
    return Analyzer(graph, vulns, installed_deps=installed).analyze()


def single(result):
    assert len(result.verdicts) == 1
    return result.verdicts[0]


class TestReachable(unittest.TestCase):
    def test_direct_reachable(self):
        verdict = single(analyze(APP_DIRECT))
        self.assertEqual(verdict.status, STATUS_REACHABLE)
        self.assertEqual(verdict.reason, "path_from_entry_exists")
        # 完整调用链：app.main -> vulnpkg.parse
        self.assertEqual(len(verdict.path), 1)
        hop = verdict.path[0]
        self.assertEqual(hop["from"], "app.main")
        self.assertEqual(hop["to"], "vulnpkg.parse")
        self.assertEqual(hop["kind"], EDGE_STATIC)
        self.assertTrue(hop["file"].endswith("app.py"))
        self.assertGreater(hop["line"], 0)

    def test_indirect_reachable_chain_verifiable(self):
        verdict = single(analyze(APP_INDIRECT))
        self.assertEqual(verdict.status, STATUS_REACHABLE)
        # 链必须是 app.main -> app.run -> vulnpkg.parse，逐跳首尾相接
        self.assertEqual([h["from"] for h in verdict.path],
                         ["app.main", "app.run"])
        self.assertEqual([h["to"] for h in verdict.path],
                         ["app.run", "vulnpkg.parse"])
        for left, right in zip(verdict.path, verdict.path[1:]):
            self.assertEqual(left["to"], right["from"])
        for hop in verdict.path:
            self.assertIn(hop["kind"],
                          (EDGE_STATIC, EDGE_DYNAMIC_IMPORT,
                           EDGE_REFLECTION, EDGE_CONFIG, "method_heuristic"))
            self.assertIsInstance(hop["line"], int)

    def test_dynamic_import_literal_reachable(self):
        verdict = single(analyze(
            APP_DYN_IMPORT, vuln_src=VULN_WITH_MAIN_CALL))
        self.assertEqual(verdict.status, STATUS_REACHABLE)
        kinds = [h["kind"] for h in verdict.path]
        self.assertIn(EDGE_DYNAMIC_IMPORT, kinds)
        # 链：app.main -> vulnpkg.<module> -> vulnpkg.parse
        self.assertEqual(verdict.path[0]["from"], "app.main")
        self.assertEqual(verdict.path[-1]["to"], "vulnpkg.parse")

    def test_reflection_literal_reachable(self):
        verdict = single(analyze(APP_REFLECTION))
        self.assertEqual(verdict.status, STATUS_REACHABLE)
        kinds = [h["kind"] for h in verdict.path]
        self.assertIn(EDGE_REFLECTION, kinds)

    def test_config_driven_reachable(self):
        verdict = single(analyze(
            APP_NEVER_REF,
            config_strings=(("plugins.json", "vulnpkg.parse"),)))
        self.assertEqual(verdict.status, STATUS_REACHABLE)
        self.assertEqual(verdict.path[0]["kind"], EDGE_CONFIG)
        self.assertEqual(verdict.path[0]["from"], "<config>")


class TestUnreachable(unittest.TestCase):
    def test_never_referenced(self):
        verdict = single(analyze(APP_NEVER_REF))
        self.assertEqual(verdict.status, STATUS_UNREACHABLE)
        self.assertEqual(verdict.reason, "never_referenced")
        self.assertIn("从未被任何调用点引用", verdict.detail)

    def test_no_path_from_entry(self):
        verdict = single(analyze(APP_NO_PATH))
        self.assertEqual(verdict.status, STATUS_UNREACHABLE)
        self.assertEqual(verdict.reason, "no_path_from_entry")
        self.assertIn("app.dead_handler",
                      verdict.evidence["referenced_from"])

    def test_blocked_by_condition(self):
        verdict = single(analyze(APP_BLOCKED))
        self.assertEqual(verdict.status, STATUS_UNREACHABLE)
        self.assertEqual(verdict.reason, "blocked_by_condition")
        blockers = verdict.evidence["blockers"]
        self.assertEqual(len(blockers), 1)
        self.assertEqual(blockers[0]["guard"], "ENABLE_LEGACY")
        self.assertEqual(blockers[0]["caller"], "app.main")

    def test_symbol_not_found(self):
        verdict = single(analyze(APP_DIRECT,
                                 vuln_symbols=("vulnpkg.nonexistent",)))
        self.assertEqual(verdict.status, STATUS_UNREACHABLE)
        self.assertEqual(verdict.reason, "symbol_not_found")


class TestDependencyMissing(unittest.TestCase):
    def test_dependency_missing(self):
        result = analyze(APP_NEVER_REF, vuln_src=None, installed=(),
                         dep="ghostpkg",
                         vuln_symbols=("ghostpkg.core.run",))
        verdict = single(result)
        self.assertEqual(verdict.status, STATUS_DEP_MISSING)
        self.assertEqual(verdict.reason, "dependency_not_installed")

    def test_installed_but_no_sources_is_unknown(self):
        result = analyze(APP_NEVER_REF, vuln_src=None,
                         installed=("vulnpkg",))
        verdict = single(result)
        self.assertEqual(verdict.status, "unknown")
        self.assertEqual(verdict.reason, "dependency_sources_not_provided")


class TestDynamicUnresolved(unittest.TestCase):
    def test_unresolved_dynamic_import_is_potential(self):
        verdict = single(analyze(APP_UNRESOLVED_DYN))
        self.assertEqual(verdict.status, STATUS_POTENTIAL)
        self.assertEqual(verdict.reason, "unresolved_dynamic_dispatch")
        notes = verdict.evidence["dynamic_notes"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0]["mechanism"], "importlib.import_module")
        self.assertEqual(notes[0]["function"], "app.main")


class TestEntryDetection(unittest.TestCase):
    def test_main_guard_module_is_entry(self):
        app = '''
from vulnpkg import parse

if __name__ == "__main__":
    parse("cli")
'''
        verdict = single(analyze(app))
        self.assertEqual(verdict.status, STATUS_REACHABLE)
        self.assertEqual(verdict.path[0]["from"], "app.<module>")

    def test_method_call_heuristic(self):
        app = '''
class Handler:
    def handle(self):
        from vulnpkg import parse
        return parse("x")


def main():
    return Handler().handle()
'''
        verdict = single(analyze(app))
        self.assertEqual(verdict.status, STATUS_REACHABLE)


if __name__ == "__main__":
    unittest.main()
