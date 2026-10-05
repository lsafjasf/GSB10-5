"""演示: 未知键清单 + 生效来源数据 + 错误分类。运行: python3 demo.py"""

import json
import os
import tempfile

from configlib import Schema, item, load_config, required

SCHEMA = Schema([
    required("host"),
    item("port", "int", default=8080),
    item("debug", "bool", default=False),
    item("log_level", "str", default="info",
         choices=["debug", "info", "warn", "error"]),
])

fd, path = tempfile.mkstemp(suffix=".json")
with os.fdopen(fd, "w", encoding="utf-8") as fh:
    json.dump({"host": "file-host", "prot": 9000, "debug": True}, fh)  # prot 是拼错的 port

res = load_config(
    SCHEMA,
    file_path=path,
    env={"APP_HOST": "env-host", "APP_PORT": "", "APP_HOSTT": "typo"},
    argv=["--log-level", "warn", "--debig", "true"],
)
os.unlink(path)

print("== 生效值与来源 ==")
for key, info in res.sources.items():
    print(f"  {key:10s} = {info.value!r:12s}  生效层: {info.layer:7s} "
          f"出现于: {','.join(info.present_in)}  各层原始值: {info.layer_values}"
          + (f"  层错误: {info.layer_errors}" if info.layer_errors else ""))

print("\n== 未知键清单 ==")
for e in res.unknown_keys:
    print(f"  [{e.layer}] {e.key!r} 原始值={e.raw_value!r} 近似建议={e.suggestions}")

print("\n== 其他错误（按类别区分） ==")
for e in res.errors:
    if e.kind != "UnknownKey":
        print(f"  [{e.kind}] 键={e.key!r} 层={e.layer} {e.message}")
