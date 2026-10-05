"""回滚验证引擎：让旧版本读取器逐个读取新版本写入的数据并汇总结论。

结论分级：
- ROLLBACK_OK      可回滚：无 error 级问题（info 级的未知字段不阻断）；
- NEEDS_CONVERSION 需先转换：问题均为可逆转换（新枚举映射、索引重建）；
- NOT_ROLLBACKABLE 不可回滚：存在字段级破坏（类型变更/必填缺失等），
                     旧版本无法直接读取，需迁移或前滚修复。
"""

import json
import time

from .oldreader import OldReader
from .schema import SCHEMA_V1

ROLLBACK_OK = "可回滚"
NEEDS_CONVERSION = "需先转换"
NOT_ROLLBACKABLE = "不可回滚"

CONVERTIBLE_KINDS = {"unknown_enum", "index_format", "index_version"}
FATAL_KINDS = {
    "type_mismatch", "missing_required_field",
    "contract_violation", "decode_error",
}

SUGGESTIONS = {
    "unknown_enum": "回滚前将新枚举值映射为 v1 已知取值（如 pending->inactive），或隔离相关记录",
    "index_format": "回滚前用 v1 工具链将索引重建为 flat-hash v1 格式",
    "index_version": "回滚前重建索引到 v1 索引版本",
    "type_mismatch": "字段类型已变更，需要外部映射表或迁移脚本，不能就地简单转换",
    "missing_required_field": "v1 必填字段缺失，需补数或放弃回滚",
    "contract_violation": "旧版本业务接口对该数据执行失败，需修复数据或修改旧代码",
    "decode_error": "存在旧版本无法解析的行，回滚前必须清洗",
    "unknown_field": "无需处理：v1 按 ignore 策略丢弃，不影响读取",
}

CONCLUSION_STEPS = {
    ROLLBACK_OK: [
        "按常规发布流程重新部署旧版本；",
        "回滚后抽查读取日志，确认无解析错误；",
        "后续若恢复新版本，注意新写入数据仍需保持向后兼容。",
    ],
    NEEDS_CONVERSION: [
        "冻结新版本写入或开启双写，避免转换期间继续产生不兼容数据；",
        "按不兼容清单逐条转换：新枚举值映射回 v1 取值；用 v1 工具链重建索引；",
        "转换后重新运行本验证，确认结论变为“可回滚”；",
        "再部署旧版本，并保留转换前数据快照。",
    ],
    NOT_ROLLBACKABLE: [
        "禁止直接回滚：旧版本无法读取该数据，直接回滚会导致读取失败/脏数据；",
        "制定迁移方案：外部映射表（新旧 id 对照）、数据迁移脚本或前滚修复 fix-forward；",
        "迁移完成并在预发环境用旧版本读取验证通过前，保持新版本运行。",
    ],
}


class Verifier:
    def __init__(self, schema=None):
        self.reader = OldReader(schema or SCHEMA_V1)

    def verify_lines(self, lines, extra_issues=()):
        """流式逐行验证。返回原始统计结构。"""
        agg = {}
        total = ok = failed = 0
        t0 = time.perf_counter()

        def absorb(issue):
            key = (issue["kind"], issue["path"], issue["severity"])
            slot = agg.get(key)
            if slot is None:
                slot = {
                    "kind": issue["kind"],
                    "path": issue["path"],
                    "severity": issue["severity"],
                    "count": 0,
                    "first_line": issue["line"],
                    "detail": issue["detail"],
                    "sample": issue.get("sample"),
                }
                agg[key] = slot
            slot["count"] += 1

        for issue in extra_issues:
            absorb(issue)

        for line_no, raw in enumerate(lines, 1):
            line = raw.strip()
            if not line:
                continue
            total += 1
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                failed += 1
                absorb({"kind": "decode_error", "path": "$", "severity": "error",
                        "line": line_no,
                        "detail": "JSON 解析失败: %s" % exc, "sample": line[:80]})
                continue

            record, issues = self.reader.read_record(obj, line_no)
            if record is not None:
                issues = issues + self.reader.check_contracts(record, line_no)
            if any(i["severity"] == "error" for i in issues):
                failed += 1
            else:
                ok += 1
            for issue in issues:
                absorb(issue)

        elapsed = time.perf_counter() - t0
        return {"total": total, "ok": ok, "failed": failed,
                "elapsed_sec": elapsed, "agg": agg}

    def verify_files(self, data_path, index_path=None):
        extra_issues = []
        if index_path:
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    extra_issues = self.reader.read_index(json.load(f))
            except (OSError, json.JSONDecodeError) as exc:
                extra_issues = [{
                    "kind": "index_format", "path": "index", "severity": "error",
                    "line": 0, "detail": "索引文件无法读取: %s" % exc, "sample": None}]

        with open(data_path, "r", encoding="utf-8") as f:
            stats = self.verify_lines(f, extra_issues)
        return stats


def _conclusion(agg):
    error_kinds = {k[0] for k in agg if k[2] == "error"}
    if not error_kinds:
        return ROLLBACK_OK
    if error_kinds & FATAL_KINDS:
        return NOT_ROLLBACKABLE
    if error_kinds <= CONVERTIBLE_KINDS:
        return NEEDS_CONVERSION
    return NOT_ROLLBACKABLE


def build_report(scenario, stats):
    agg = stats.pop("agg")
    entries = sorted(agg.values(), key=lambda x: (x["severity"] != "error", -x["count"]))
    for entry in entries:
        entry["suggestion"] = SUGGESTIONS.get(entry["kind"], "人工确认")
    conclusion = _conclusion(agg)
    return {
        "scenario": scenario,
        "total": stats["total"],
        "read_ok": stats["ok"],
        "read_failed": stats["failed"],
        "verify_elapsed_sec": round(stats["elapsed_sec"], 3),
        "conclusion": conclusion,
        "handling_steps": CONCLUSION_STEPS[conclusion],
        "incompatibilities": [e for e in entries if e["severity"] == "error"],
        "informational": [e for e in entries if e["severity"] == "info"],
    }
