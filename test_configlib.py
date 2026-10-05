"""configlib 自测（仅标准库 unittest）。运行: python3 test_configlib.py -v"""

import json
import os
import tempfile
import unittest

from configlib import (ConfigError, Schema, item, load_config, required,
                       parse_cli_args)

SCHEMA = Schema([
    required("host"),
    item("port", "int", default=8080),
    item("debug", "bool", default=False),
    item("workers", "int", default=4),
    item("tags", "list", default=[]),
    item("log_level", "str", default="info",
         choices=["debug", "info", "warn", "error"]),
    item("timeout", "float", default=1.5),
    item("meta", "dict"),  # 可选、无默认
])


def write_file(data):
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return path


def load(**kw):
    kw.setdefault("env", {})
    kw.setdefault("argv", [])
    return load_config(SCHEMA, **kw)


class TestAllDefaults(unittest.TestCase):
    """情形 1: 全部来自默认（必填项由文件满足，其余走默认）。"""

    def test_defaults(self):
        path = write_file({"host": "db.local"})
        try:
            res = load(file_path=path)
        finally:
            os.unlink(path)
        self.assertEqual(res.errors, [])
        self.assertEqual(res.values["port"], 8080)
        self.assertEqual(res.values["debug"], False)
        self.assertEqual(res.sources["port"].layer, "default")
        self.assertEqual(res.sources["debug"].layer, "default")
        self.assertEqual(res.sources["host"].layer, "file")
        self.assertNotIn("meta", res.values)  # 可选无默认 -> 缺省


class TestOverrideChain(unittest.TestCase):
    """情形 2: 同一键被多层覆盖，高优先级生效，来源可追溯。"""

    def test_override(self):
        path = write_file({"host": "file-host", "port": 9000, "debug": True})
        try:
            res = load(file_path=path,
                       env={"APP_HOST": "env-host", "APP_PORT": "9100"},
                       argv=["--port", "9200"])
        finally:
            os.unlink(path)
        self.assertEqual(res.errors, [])
        # 优先级: cli > env > file > default
        self.assertEqual(res.values["port"], 9200)
        self.assertEqual(res.sources["port"].layer, "cli")
        self.assertEqual(res.values["host"], "env-host")
        self.assertEqual(res.sources["host"].layer, "env")
        self.assertEqual(res.values["debug"], True)
        self.assertEqual(res.sources["debug"].layer, "file")
        # 生效来源数据: present_in 与 layer_values
        self.assertEqual(res.sources["port"].present_in,
                         ["default", "file", "env", "cli"])
        self.assertEqual(res.sources["port"].layer_values,
                         {"default": 8080, "file": 9000, "env": "9100",
                          "cli": "9200"})

    def test_env_types(self):
        res = load(env={"APP_HOST": "h", "APP_PORT": "1234",
                        "APP_DEBUG": "yes", "APP_TIMEOUT": "2.5",
                        "APP_TAGS": '["a", "b"]', "APP_META": '{"x": 1}'})
        self.assertEqual(res.errors, [])
        self.assertEqual(res.values["port"], 1234)
        self.assertIs(res.values["debug"], True)
        self.assertEqual(res.values["timeout"], 2.5)
        self.assertEqual(res.values["tags"], ["a", "b"])
        self.assertEqual(res.values["meta"], {"x": 1})
        self.assertEqual(res.sources["tags"].layer, "env")

    def test_cli_equals_and_separate(self):
        res = load(argv=["--host=cli-host", "--port", "7777"])
        self.assertEqual(res.errors, [])
        self.assertEqual(res.values["host"], "cli-host")
        self.assertEqual(res.sources["host"].layer, "cli")
        self.assertEqual(res.values["port"], 7777)


class TestUnknownKeys(unittest.TestCase):
    """情形 3: 未知键（含拼写近似）被识别并报告。"""

    def test_unknown_file_env_cli(self):
        path = write_file({"host": "h", "prot": 123, "totally_unknown": 1})
        try:
            res = load(file_path=path,
                       env={"APP_HOSTT": "typo"},
                       argv=["--debig", "true"])
        finally:
            os.unlink(path)
        keys = sorted(e.key for e in res.unknown_keys)
        self.assertEqual(keys, ["debig", "hostt", "prot", "totally_unknown"])
        by_key = {e.key: e for e in res.unknown_keys}
        # 拼写近似建议
        self.assertIn("port", by_key["prot"].suggestions)
        self.assertIn("host", by_key["hostt"].suggestions)
        self.assertIn("debug", by_key["debig"].suggestions)
        self.assertEqual(by_key["totally_unknown"].suggestions, [])
        # 未知键不进入生效值
        self.assertNotIn("prot", res.values)
        # 未知键也计入 errors
        self.assertTrue(all(e.kind == "UnknownKey" for e in res.unknown_keys))


class TestMissingRequired(unittest.TestCase):
    """情形 4: 必填项缺失。"""

    def test_missing_required(self):
        res = load()
        kinds = [e.kind for e in res.errors]
        self.assertIn("MissingRequired", kinds)
        miss = [e for e in res.errors if e.kind == "MissingRequired"]
        self.assertEqual([e.key for e in miss], ["host"])
        self.assertNotIn("host", res.values)

    def test_required_filled_by_any_layer(self):
        for kw in ({"env": {"APP_HOST": "h"}},
                   {"argv": ["--host", "h"]}):
            res = load(**kw)
            self.assertEqual(res.errors, [])
            self.assertEqual(res.values["host"], "h")

    def test_require_ok_raises(self):
        with self.assertRaises(ConfigError) as ctx:
            load().require_ok()
        self.assertTrue(any(e.kind == "MissingRequired"
                            for e in ctx.exception.errors))


class TestEmptyVsTypeMismatch(unittest.TestCase):
    """空字符串与类型不符是两种不同判定，且都不覆盖低层合法值。"""

    def test_empty_string(self):
        path = write_file({"host": "file-host", "port": 9000})
        try:
            res = load(file_path=path, env={"APP_HOST": "", "APP_PORT": ""})
        finally:
            os.unlink(path)
        kinds = {(e.key, e.kind) for e in res.errors}
        self.assertIn(("host", "EmptyString"), kinds)
        self.assertIn(("port", "EmptyString"), kinds)
        self.assertNotIn(("port", "TypeMismatch"), kinds)
        # 空串不覆盖低层合法值
        self.assertEqual(res.values["host"], "file-host")
        self.assertEqual(res.sources["host"].layer, "file")
        self.assertEqual(res.values["port"], 9000)
        # 层错误记录在来源数据中
        self.assertEqual(res.sources["host"].layer_errors, {"env": "EmptyString"})

    def test_type_mismatch(self):
        res = load(env={"APP_HOST": "h", "APP_PORT": "abc",
                        "APP_DEBUG": "maybe", "APP_TAGS": "not-json"})
        kinds = {e.key: e.kind for e in res.errors}
        self.assertEqual(kinds["port"], "TypeMismatch")
        self.assertEqual(kinds["debug"], "TypeMismatch")
        self.assertEqual(kinds["tags"], "TypeMismatch")
        # 非法高层值不覆盖默认值
        self.assertEqual(res.values["port"], 8080)
        self.assertEqual(res.sources["port"].layer, "default")

    def test_file_layer_type_check(self):
        path = write_file({"host": "h", "port": "9000", "debug": 1})
        try:
            res = load(file_path=path)
        finally:
            os.unlink(path)
        kinds = {e.key: e.kind for e in res.errors}
        # 文件层不做隐式转换: 字符串 "9000" 不是 int, 1 不是 bool
        self.assertEqual(kinds["port"], "TypeMismatch")
        self.assertEqual(kinds["debug"], "TypeMismatch")
        self.assertEqual(res.values["port"], 8080)

    def test_empty_required_is_empty_not_missing(self):
        res = load(env={"APP_HOST": ""})
        kinds = [e.kind for e in res.errors if e.key == "host"]
        self.assertIn("EmptyString", kinds)
        self.assertIn("MissingRequired", kinds)  # 空串不算有效值 -> 仍缺失
        self.assertNotIn("host", res.values)


class TestChoices(unittest.TestCase):
    def test_choices(self):
        res = load(env={"APP_HOST": "h", "APP_LOG_LEVEL": "verbose"})
        kinds = {e.key: e.kind for e in res.errors}
        self.assertEqual(kinds["log_level"], "TypeMismatch")
        self.assertEqual(res.values["log_level"], "info")  # 回退默认

        res2 = load(env={"APP_HOST": "h", "APP_LOG_LEVEL": "warn"})
        self.assertEqual(res2.errors, [])
        self.assertEqual(res2.values["log_level"], "warn")


class TestCliParser(unittest.TestCase):
    def test_parse(self):
        out = parse_cli_args(["--a", "1", "--b=2", "--c"])
        self.assertEqual(out, {"a": "1", "b": "2", "c": ""})


if __name__ == "__main__":
    unittest.main()
