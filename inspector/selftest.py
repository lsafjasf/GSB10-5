"""自测：python3 -m inspector.selftest  （小上界，整套约 10 秒）"""
import unittest

from inspector import (Engine, Limits, Rule, run_rule,
                       STATUS_OK, STATUS_FAILED, STATUS_ERROR, STATUS_TIMEOUT,
                       STATUS_MEMORY, STATUS_OUTPUT, STATUS_REJECTED)

FAST = Limits(wall_time_s=1.0, cpu_time_s=1, memory_mb=64,
              output_bytes=4 * 1024, result_bytes=1024)


class TestSandbox(unittest.TestCase):
    def test_normal_pass_and_fail(self):
        r1 = run_rule("ok", "def check(facts):\n    return facts['cpu'] < 90",
                      {"cpu": 42}, FAST)
        self.assertEqual(r1["status"], STATUS_OK, r1)
        r2 = run_rule("bad", "def check(facts):\n    return facts['cpu'] < 90",
                      {"cpu": 99}, FAST)
        self.assertEqual(r2["status"], STATUS_FAILED, r2)

    def test_infinite_loop_is_killed(self):
        r = run_rule("loop", "def check(facts):\n    while True:\n        pass",
                     None, FAST)
        self.assertEqual(r["status"], STATUS_TIMEOUT, r)
        self.assertLess(r["duration_ms"], 3000)

    def test_catastrophic_regex_is_killed(self):
        src = ("import re\n"  # 会被 REJECTED，改用注入的 re
               )
        src = ("def check(facts):\n"
               "    return re.match(r'(a+)+$', 'a' * 28 + 'b')\n")
        r = run_rule("regex", src, None, FAST)
        self.assertEqual(r["status"], STATUS_TIMEOUT, r)

    def test_huge_output_is_capped(self):
        src = ("def check(facts):\n"
               "    for i in range(10 ** 7):\n"
               "        print(i)\n"
               "    return True\n")
        r = run_rule("loud", src, None, FAST)
        self.assertEqual(r["status"], STATUS_OUTPUT, r)
        self.assertLessEqual(len(r["output"]), FAST.output_bytes)

    def test_memory_bomb_is_contained(self):
        src = "def check(facts):\n    x = [0] * (10 ** 9)\n    return True\n"
        r = run_rule("bomb", src, None, FAST)
        self.assertEqual(r["status"], STATUS_MEMORY, r)

    def test_forbidden_capabilities(self):
        cases = {
            "import": "import os\ndef check(facts):\n    return True\n",
            "dunder": "def check(facts):\n    return ().__class__.__bases__\n",
            "open": "def check(facts):\n    return open('/etc/passwd').read()\n",
            "eval": "def check(facts):\n    return eval('1+1')\n",
            "import_func": "def check(facts):\n    return __import__('os')\n",
        }
        for name, src in cases.items():
            r = run_rule(name, src, None, FAST)
            self.assertIn(r["status"], (STATUS_REJECTED, STATUS_ERROR),
                          f"{name}: {r}")
            self.assertNotEqual(r["status"], STATUS_OK, f"{name} escaped!")

    def test_exception_isolated(self):
        r = run_rule("boom", "def check(facts):\n    return 1 / 0\n", None, FAST)
        self.assertEqual(r["status"], STATUS_ERROR, r)
        self.assertIn("ZeroDivisionError", r["error"])


class TestEnginePartialFailure(unittest.TestCase):
    def test_one_bad_rule_does_not_drag_others(self):
        rules = [
            Rule("good-1", "def check(facts):\n    return True\n"),
            Rule("good-2", "def check(facts):\n    return facts['x'] == 1\n",
                 facts={"x": 1}),
            Rule("biz-fail", "def check(facts):\n    return False\n"),
            Rule("dead-loop", "def check(facts):\n    while True:\n        pass\n"),
            Rule("crash", "def check(facts):\n    raise ValueError('bad')\n"),
            Rule("evil", "import os\ndef check(facts):\n    return True\n"),
        ]
        engine = Engine(workers=4, limits=FAST)
        report = engine.run(rules)
        summary = report.summary
        self.assertEqual(summary["total"], 6)
        self.assertEqual(summary["by_status"].get(STATUS_OK), 2, summary)
        self.assertEqual(summary["by_status"].get(STATUS_FAILED), 1, summary)
        self.assertEqual(summary["by_status"].get(STATUS_TIMEOUT), 1, summary)
        self.assertEqual(summary["by_status"].get(STATUS_ERROR), 1, summary)
        self.assertEqual(summary["by_status"].get(STATUS_REJECTED), 1, summary)
        self.assertFalse(summary["all_passed"])
        # 部分失败时仍给出每条规则的明确结果
        for r in report.results:
            self.assertTrue(r.status)
            self.assertIsNotNone(r.duration_ms)


if __name__ == "__main__":
    unittest.main(verbosity=2)
