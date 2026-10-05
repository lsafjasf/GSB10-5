"""configkit 自测：python3 -m unittest -v"""

import json
import os
import tempfile
import unittest

from configkit import (
    ConfigError, Field, KIND_EMPTY_VALUE, KIND_MISSING_REQUIRED,
    KIND_TYPE_ERROR, KIND_UNKNOWN_KEY, load, load_or_raise, parse_cli_args,
)

SCHEMA = {
    "db_host": Field(str, required=True, doc="数据库主机"),
    "db_port": Field(int, default=5432),
    "debug": Field(bool, default=False),
    "name": Field(str, default="svc"),
    "ratio": Field(float, default=0.5),
}

ENV = {
    "APP_DB_HOST": "env-host",
    "APP_DB_PORT": "6000",
    "APP_DEBUG": "true",
}


def kinds(result):
    return [i.kind for i in result.issues]


class TestAllFromDefault(unittest.TestCase):
    """情形1：全部来自默认值（仅必填键由 env 提供）。"""

    def test_defaults(self):
        r = load(SCHEMA, env={"APP_DB_HOST": "h"}, file_data={}, cli_data={})
        self.assertTrue(r.ok, r.issues)
        self.assertEqual(r.values["db_port"], 5432)
        self.assertEqual(r.values["debug"], False)
        self.assertEqual(r.sources["db_port"], "default")
        self.assertEqual(r.sources["debug"], "default")
        self.assertEqual(r.sources["name"], "default")
        self.assertEqual(r.sources["db_host"], "env")


class TestOverridePriority(unittest.TestCase):
    """情形2：高层覆盖低层，优先级 cli > env > file > default。"""

    def test_file_overrides_default(self):
        r = load(SCHEMA, env={"APP_DB_HOST": "h"},
                 file_data={"db_port": 1111}, cli_data={})
        self.assertEqual(r.values["db_port"], 1111)
        self.assertEqual(r.sources["db_port"], "file")
        self.assertEqual(r.overridden["db_port"], ["default"])

    def test_env_overrides_file(self):
        r = load(SCHEMA, env=ENV, file_data={"db_port": 1111, "debug": False},
                 cli_data={})
        self.assertEqual(r.values["db_port"], 6000)
        self.assertEqual(r.sources["db_port"], "env")
        self.assertEqual(r.overridden["db_port"], ["file", "default"])
        self.assertEqual(r.values["debug"], True)
        self.assertEqual(r.sources["debug"], "env")

    def test_cli_overrides_env(self):
        r = load(SCHEMA, env=ENV, file_data={"db_port": 1111},
                 cli_data={"db_port": "9999"})
        self.assertEqual(r.values["db_port"], 9999)
        self.assertEqual(r.sources["db_port"], "cli")
        self.assertEqual(r.overridden["db_port"], ["env", "file", "default"])

    def test_cli_args_parsing(self):
        r = load(SCHEMA, env={"APP_DB_HOST": "h"},
                 cli_args=["--db-port=7777", "--debug", "yes"])
        self.assertEqual(r.values["db_port"], 7777)
        self.assertEqual(r.values["debug"], True)
        self.assertEqual(r.sources["db_port"], "cli")

    def test_dash_to_underscore(self):
        self.assertEqual(parse_cli_args(["--db-host=x"]), {"db_host": "x"})


class TestUnknownKeys(unittest.TestCase):
    """情形3：未知键（含拼写近似）必须被识别并报告。"""

    def test_unknown_in_file(self):
        r = load(SCHEMA, env={"APP_DB_HOST": "h"},
                 file_data={"db_hots": "typo", "totally_unknown": 1}, cli_data={})
        unknown = [i for i in r.issues if i.kind == KIND_UNKNOWN_KEY]
        self.assertEqual({i.key for i in unknown}, {"db_hots", "totally_unknown"})
        near = next(i for i in unknown if i.key == "db_hots")
        self.assertIn("db_host", near.suggestions)  # 拼写近似建议
        far = next(i for i in unknown if i.key == "totally_unknown")
        self.assertEqual(far.suggestions, ())
        self.assertEqual(r.unknown_keys(), ["db_hots", "totally_unknown"])

    def test_unknown_in_env_and_cli(self):
        env = dict(ENV)
        env["APP_DBUG"] = "1"  # debug 的拼写错误
        r = load(SCHEMA, env=env, cli_data={"db_pott": "1"})
        keys = {i.key for i in r.issues if i.kind == KIND_UNKNOWN_KEY}
        self.assertEqual(keys, {"dbug", "db_pott"})
        sugg = {i.key: i.suggestions for i in r.issues if i.kind == KIND_UNKNOWN_KEY}
        self.assertIn("debug", sugg["dbug"])
        self.assertIn("db_port", sugg["db_pott"])


class TestMissingRequired(unittest.TestCase):
    """情形4：必填项缺失。"""

    def test_missing_required(self):
        r = load(SCHEMA, env={}, file_data={"db_port": 1}, cli_data={})
        self.assertIn(KIND_MISSING_REQUIRED, kinds(r))
        miss = next(i for i in r.issues if i.kind == KIND_MISSING_REQUIRED)
        self.assertEqual(miss.key, "db_host")
        self.assertNotIn("db_host", r.values)
        self.assertFalse(r.ok)

    def test_raise_if_failed(self):
        with self.assertRaises(ConfigError) as ctx:
            load_or_raise(SCHEMA, env={}, cli_data={})
        self.assertTrue(any(i.kind == KIND_MISSING_REQUIRED
                            for i in ctx.exception.issues))


class TestEmptyVsTypeError(unittest.TestCase):
    """空字符串与类型不符是两种不同判定。"""

    def test_empty_string_distinct_from_type_error(self):
        r = load(SCHEMA,
                 env={"APP_DB_HOST": "h", "APP_DB_PORT": ""},   # 空串
                 file_data={"ratio": "not-a-float"},            # 类型错
                 cli_data={})
        by_key = {i.key: i for i in r.issues}
        self.assertEqual(by_key["db_port"].kind, KIND_EMPTY_VALUE)
        self.assertEqual(by_key["ratio"].kind, KIND_TYPE_ERROR)
        self.assertEqual(by_key["ratio"].expected, "float")
        self.assertEqual(by_key["ratio"].actual, "str")

    def test_empty_falls_back_to_lower_layer(self):
        r = load(SCHEMA,
                 env={"APP_DB_HOST": "h", "APP_DB_PORT": ""},
                 file_data={"db_port": 4444}, cli_data={})
        # env 层空串被忽略，file 层生效
        self.assertEqual(r.values["db_port"], 4444)
        self.assertEqual(r.sources["db_port"], "file")
        self.assertIn(KIND_EMPTY_VALUE, kinds(r))

    def test_empty_required_becomes_missing(self):
        r = load(SCHEMA, env={"APP_DB_HOST": "  "}, cli_data={})
        ks = kinds(r)
        self.assertIn(KIND_EMPTY_VALUE, ks)
        self.assertIn(KIND_MISSING_REQUIRED, ks)

    def test_type_error_falls_back_to_default(self):
        r = load(SCHEMA, env={"APP_DB_HOST": "h", "APP_DB_PORT": "abc"},
                 cli_data={})
        self.assertEqual(r.values["db_port"], 5432)
        self.assertEqual(r.sources["db_port"], "default")
        self.assertEqual(r.overridden["db_port"], ["env"])

    def test_bool_rejected_as_int(self):
        r = load(SCHEMA, env={"APP_DB_HOST": "h"},
                 file_data={"db_port": True}, cli_data={})
        self.assertIn(KIND_TYPE_ERROR, kinds(r))
        self.assertEqual(r.values["db_port"], 5432)  # 回退默认


class TestFileLoading(unittest.TestCase):
    def test_load_from_json_file(self):
        with tempfile.NamedTemporaryFile(
                "w", suffix=".json", delete=False) as fh:
            json.dump({"db_host": "file-host", "db_port": 1234}, fh)
            path = fh.name
        try:
            r = load(SCHEMA, env={}, file_path=path, cli_data={})
            self.assertEqual(r.values["db_host"], "file-host")
            self.assertEqual(r.sources["db_host"], "file")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
