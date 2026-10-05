"""旧版本 (v1) 读取器 —— 模拟旧版本代码对新版本数据的真实读取路径。

校验严格度按“旧版本代码实际行为”设定：
- 必填字段缺失 / 类型不符：旧代码直接报错（error）；
- 枚举出现未知取值：旧状态机无对应分支（error）；
- 未知字段：按 ignore 策略丢弃，仅记为 info（不影响读取结果）；
- 索引：仅识别 flat-hash v1，其余结构无法定位数据（error）。
接口契约则通过真实执行 v1 业务函数验证。
"""

from .schema import STATUS_V1

_PY_TYPE = {"int": int, "str": str, "list": list, "dict": dict}


def make_issue(kind, path, detail, line_no=0, severity="error", sample=None):
    return {
        "kind": kind,
        "path": path,
        "detail": detail,
        "line": line_no,
        "severity": severity,
        "sample": sample,
    }


class OldReader:
    """按 v1 模式读取记录的旧版本读取器。"""

    def __init__(self, schema):
        self.schema = schema
        self.fields = schema["fields"]

    def read_record(self, obj, line_no=0):
        """返回 (record, issues)；存在 error 时 record 为 None。"""
        issues = []
        record = {}
        for name, spec in self.fields.items():
            if name not in obj:
                if spec["required"]:
                    issues.append(make_issue(
                        "missing_required_field", name,
                        "v1 必填字段缺失", line_no,
                        sample="<absent>"))
                continue
            value = obj[name]
            kind = spec["type"]
            if kind == "enum":
                if not isinstance(value, str):
                    issues.append(make_issue(
                        "type_mismatch", name,
                        "枚举字段期望 str，实际 %s" % type(value).__name__,
                        line_no, sample=repr(value)[:80]))
                elif value not in spec["values"]:
                    issues.append(make_issue(
                        "unknown_enum", name,
                        "取值 %r 不在 v1 枚举集合 %s 内" % (value, list(spec["values"])),
                        line_no, sample=value))
                else:
                    record[name] = value
            else:
                py_type = _PY_TYPE[kind]
                # bool 是 int 的子类，单独排除
                bad = not isinstance(value, py_type) or (kind == "int" and isinstance(value, bool))
                if bad:
                    issues.append(make_issue(
                        "type_mismatch", name,
                        "期望 %s，实际 %s" % (kind, type(value).__name__),
                        line_no, sample=repr(value)[:80]))
                else:
                    record[name] = value

        if self.schema.get("unknown_field_policy") == "ignore":
            for name in obj:
                if name not in self.fields:
                    issues.append(make_issue(
                        "unknown_field", name,
                        "v1 未定义该字段，按 ignore 策略丢弃",
                        line_no, severity="info", sample=repr(obj[name])[:80]))
        else:
            for name in obj:
                if name not in self.fields:
                    issues.append(make_issue(
                        "unknown_field", name, "v1 严格模式拒绝未知字段", line_no))

        if any(i["severity"] == "error" for i in issues):
            return None, issues
        return record, issues

    def read_index(self, obj):
        """校验索引文件结构。"""
        want = self.schema["index"]
        issues = []
        if obj.get("format") != want["format"]:
            issues.append(make_issue(
                "index_format", "index.format",
                "v1 仅支持 %s，实际为 %r" % (want["format"], obj.get("format")),
                sample=obj.get("format")))
        if obj.get("version") != want["version"]:
            issues.append(make_issue(
                "index_version", "index.version",
                "v1 仅支持索引版本 %s，实际为 %r" % (want["version"], obj.get("version")),
                sample=obj.get("version")))
        return issues

    # ---- 接口契约面：旧版本业务代码对数据的真实依赖 ----

    def status_rank(self, record):
        # v1 排序/权限代码：未知枚举会抛 ValueError
        return STATUS_V1.index(record["status"])

    def render_label(self, record):
        # v1 展示代码：依赖 name 为字符串、email 可选
        return "%s <%s>" % (record["name"], record.get("email", ""))

    CONTRACTS = ("status_rank", "render_label")

    def check_contracts(self, record, line_no=0):
        """对成功读出的记录实际执行 v1 业务函数。"""
        issues = []
        for fname in self.CONTRACTS:
            try:
                getattr(self, fname)(record)
            except Exception as exc:  # noqa: BLE001 旧代码真实抛出的任何异常都算违约
                issues.append(make_issue(
                    "contract_violation", fname,
                    "旧接口 %s 执行失败: %s: %s"
                    % (fname, type(exc).__name__, exc), line_no))
        return issues
