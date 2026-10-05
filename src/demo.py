"""演示：未知键清单 + 每个生效值的来源层。运行: python3 demo.py"""

import json
import tempfile
import os

from configkit import Field, load

SCHEMA = {
    "db_host": Field(str, required=True, doc="数据库主机"),
    "db_port": Field(int, default=5432),
    "debug": Field(bool, default=False),
    "log_level": Field(str, default="info"),
}

file_data = {
    "db_host": "file-db.internal",
    "db_port": 5433,
    "db_hots": "typo.example.com",   # 拼写错误的未知键
    "timeout": 30,                    # 完全未知的键
}
env = {
    "APP_DB_HOST": "env-db.internal",  # 覆盖 file
    "APP_DB_PORT": "",                 # 空字符串：忽略并回退
    "APP_DBUG": "1",                   # 未知键（debug 的拼写近似）
}
cli_args = ["--db-port=6000"]          # 覆盖 file 的 5433

with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
    json.dump(file_data, fh)
    path = fh.name

result = load(SCHEMA, env=env, file_path=path, cli_args=cli_args)
os.unlink(path)

print("=== 生效值与来源 ===")
for key in SCHEMA:
    overridden = result.overridden.get(key, [])
    chain = f"（覆盖链: {' > '.join([result.sources[key]] + overridden)}）" if overridden else ""
    print(f"  {key:10s} = {result.values.get(key)!r:22s} 来源: {result.sources.get(key)} {chain}")

print("\n=== 未知键清单 ===")
for issue in result.issues:
    if issue.kind == "unknown_key":
        sugg = f" -> 是否想写: {', '.join(issue.suggestions)}" if issue.suggestions else ""
        print(f"  [{issue.layer}] {issue.key}{sugg}")

print("\n=== 其他问题 ===")
for issue in result.issues:
    if issue.kind != "unknown_key":
        print(f"  [{issue.kind}] {issue.key} (层 {issue.layer}): {issue.message}")
