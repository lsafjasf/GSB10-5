"""巡检规则沙箱自测：python3 -m unittest discover -s tests -v"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from inspector.limits import (
    STATUS_ASSERT_FAILED,
    STATUS_BAD_RULE,
    STATUS_OK,
    STATUS_OUTPUT_LIMIT,
    STATUS_RULE_ERROR,
    STATUS_TIMEOUT,
)
from inspector.runner import run_rules


def run_one(source, data=None):
    summary = run_rules([("t", source)], data=data, workers=1)
    return summary.results[0]


class TestNormalRules(unittest.TestCase):
    def test_simple_return(self):
        r = run_one("def check(data):\n    return 1 + 2")
        self.assertEqual(r.status, STATUS_OK)
        self.assertEqual(r.result, 3)

    def test_allowed_module_re(self):
        r = run_one(
            "import re\n"
            "def check(data):\n"
            "    return re.findall(r'\\d+', data)"
        , data="a1b22c333")
        self.assertEqual(r.status, STATUS_OK)
        self.assertEqual(r.result, ["1", "22", "333"])

    def test_allowed_modules_json_math(self):
        r = run_one(
            "import json, math\n"
            "def check(data):\n"
            "    return json.loads(json.dumps({'r': math.sqrt(16)}))"
        )
        self.assertEqual(r.status, STATUS_OK)
        self.assertEqual(r.result, {"r": 4.0})

    def test_assert_pass_and_fail(self):
        ok = run_one("def check(data):\n    assert data > 0", data=1)
        self.assertEqual(ok.status, STATUS_OK)
        bad = run_one("def check(data):\n    assert data > 0, '必须为正'", data=-1)
        self.assertEqual(bad.status, STATUS_ASSERT_FAILED)
        self.assertIn("必须为正", bad.error)

    def test_print_output_collected(self):
        r = run_one("def check(data):\n    print('hello inspector')\n    return 0")
        self.assertEqual(r.status, STATUS_OK)
        self.assertGreater(r.output_bytes, 0)


class TestCapabilityRestrictions(unittest.TestCase):
    """能力清单负向用例：清单外的能力一律不可用。"""

    def test_no_open(self):
        r = run_one("def check(data):\n    return open('/etc/passwd').read()")
        self.assertEqual(r.status, STATUS_BAD_RULE)

    def test_no_import_os(self):
        r = run_one("import os\ndef check(data):\n    return 0")
        self.assertEqual(r.status, STATUS_BAD_RULE)

    def test_no_import_sys(self):
        r = run_one("import sys\ndef check(data):\n    return 0")
        self.assertEqual(r.status, STATUS_BAD_RULE)

    def test_no_from_os_import(self):
        r = run_one("from os import listdir\ndef check(data):\n    return 0")
        self.assertEqual(r.status, STATUS_BAD_RULE)

    def test_no_eval_exec_compile(self):
        for name in ("eval", "exec", "compile"):
            r = run_one(f"def check(data):\n    return {name}('1')")
            self.assertEqual(r.status, STATUS_BAD_RULE, name)

    def test_no_dunder_attribute_escape(self):
        r = run_one("def check(data):\n    return ().__class__.__base__")
        self.assertEqual(r.status, STATUS_BAD_RULE)

    def test_no_subclasses_escape(self):
        r = run_one(
            "def check(data):\n"
            "    return object.__subclasses__()"
        )
        self.assertEqual(r.status, STATUS_BAD_RULE)

    def test_no_globals_locals_vars(self):
        for name in ("globals", "locals", "vars"):
            r = run_one(f"def check(data):\n    return {name}()")
            self.assertEqual(r.status, STATUS_BAD_RULE, name)

    def test_no_getattr_setattr(self):
        r = run_one("def check(data):\n    return getattr(str, 'x')")
        self.assertEqual(r.status, STATUS_BAD_RULE)

    def test_no_type_metaclass(self):
        r = run_one("def check(data):\n    return type('X', (), {})")
        self.assertEqual(r.status, STATUS_BAD_RULE)

    def test_no_dunder_name(self):
        r = run_one("def check(data):\n    return __import__('os')")
        self.assertEqual(r.status, STATUS_BAD_RULE)

    def test_oversized_source_rejected(self):
        r = run_one("def check(data):\n    return 0  # " + "x" * (70 * 1024))
        self.assertEqual(r.status, STATUS_BAD_RULE)


class TestResourceLimits(unittest.TestCase):
    """资源上界：死循环/内存炸弹/输出炸弹都必须被中断且有明确结果。"""

    def test_infinite_loop_interrupted(self):
        r = run_one("def check(data):\n    while True:\n        pass")
        self.assertEqual(r.status, STATUS_TIMEOUT)
        self.assertIsNotNone(r.error)
        self.assertLess(r.duration_ms, 5000, "必须在有限时间内被中断")

    def test_catastrophic_regex_interrupted(self):
        # 指数级回溯的超长匹配
        r = run_one(
            "import re\n"
            "def check(data):\n"
            "    return re.match(r'(a+)+$', 'a' * 30 + 'b')"
        )
        self.assertEqual(r.status, STATUS_TIMEOUT)
        self.assertLess(r.duration_ms, 5000)

    def test_memory_bomb(self):
        r = run_one(
            "def check(data):\n"
            "    x = []\n"
            "    while True:\n"
            "        x.append('A' * 10**6)"
        )
        self.assertEqual(r.status, STATUS_RULE_ERROR)
        self.assertIn("MemoryError", r.error)

    def test_output_bomb(self):
        r = run_one(
            "def check(data):\n"
            "    for i in range(10**7):\n"
            "        print('x' * 1000)"
        )
        self.assertEqual(r.status, STATUS_OUTPUT_LIMIT)

    def test_runtime_error_reported(self):
        r = run_one("def check(data):\n    return 1 / 0")
        self.assertEqual(r.status, STATUS_RULE_ERROR)
        self.assertIn("ZeroDivisionError", r.error)


class TestIsolationAndAggregation(unittest.TestCase):
    """一条规则失败不得影响其它规则；部分失败要有汇总。"""

    def test_partial_failure_summary(self):
        rules = [
            ("ok1", "def check(data):\n    return 'fine'"),
            ("loop", "def check(data):\n    while True:\n        pass"),
            ("ok2", "def check(data):\n    return data * 2"),
            ("boom", "def check(data):\n    raise ValueError('炸了')"),
            ("bad", "import os\ndef check(data):\n    return 0"),
        ]
        s = run_rules(rules, data=21, workers=4)
        self.assertEqual(s.total, 5)
        by_id = {r.rule_id: r for r in s.results}
        # 失败规则旁边的正常规则不受影响
        self.assertEqual(by_id["ok1"].status, STATUS_OK)
        self.assertEqual(by_id["ok2"].status, STATUS_OK)
        self.assertEqual(by_id["ok2"].result, 42)
        self.assertEqual(by_id["loop"].status, STATUS_TIMEOUT)
        self.assertEqual(by_id["boom"].status, STATUS_RULE_ERROR)
        self.assertEqual(by_id["bad"].status, STATUS_BAD_RULE)
        # 汇总计数完整
        self.assertEqual(sum(s.counts.values()), 5)
        self.assertEqual(s.counts[STATUS_OK], 2)
        self.assertGreater(s.peak_rss_kb, 0)


if __name__ == "__main__":
    unittest.main()
