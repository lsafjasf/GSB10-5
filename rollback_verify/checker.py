"""Compares old/new schemas, classifies read issues, derives the rollback verdict."""
from . import reader as R

VERDICT_OK = "ROLLBACK_OK"
VERDICT_CONVERT = "NEEDS_CONVERSION"
VERDICT_NO_ROLLBACK = "NOT_ROLLBACKABLE"

VERDICT_LABELS = {
    VERDICT_OK: "可回滚",
    VERDICT_CONVERT: "需先转换",
    VERDICT_NO_ROLLBACK: "不可回滚",
}


class CompatibilityChecker:
    def __init__(self, old_schema, new_schema, enum_mappings=None):
        self.old = old_schema
        self.new = new_schema
        self.reader = R.OldVersionReader(old_schema)
        self.enum_mappings = enum_mappings or {}

    def check_record(self, record, record_seq=0):
        issues = self.reader.read_record(record, record_seq)
        return [self._classify(i) for i in issues]

    def check_index_files(self, index_files):
        out = []
        for data in index_files:
            _, issues = self.reader.read_index_file(data)
            out.extend(issues)
        return out

    def check_contract(self):
        issues = []
        new_ops = self.new.contract_map()
        for op_name, old_op in self.old.contract_map().items():
            new_op = new_ops.get(op_name)
            if new_op is None:
                issues.append(R.Issue(
                    R.SURFACE_CONTRACT, R.KIND_CONTRACT_OP_MISSING,
                    "contract.%s" % op_name, "operation exists", "<removed>"))
                continue
            new_req = {f.name: f for f in new_op.request_fields}
            new_resp = {f.name: f for f in new_op.response_fields}
            for f in old_op.request_fields:
                issues.extend(self._contract_field(op_name, "request", f, new_req.get(f.name)))
            for f in old_op.response_fields:
                issues.extend(self._contract_field(op_name, "response", f, new_resp.get(f.name)))
        return issues

    @staticmethod
    def _contract_field(op_name, direction, old_field, new_field):
        path = "contract.%s.%s.%s" % (op_name, direction, old_field.name)
        if new_field is None:
            return [R.Issue(R.SURFACE_CONTRACT, R.KIND_CONTRACT_MISSING,
                            path, R.ftype_label(old_field), "<removed>")]
        if new_field.ftype != old_field.ftype:
            return [R.Issue(R.SURFACE_CONTRACT, R.KIND_CONTRACT_TYPE,
                            path, old_field.ftype, new_field.ftype)]
        return []

    def _classify(self, issue):
        if issue.kind == R.KIND_ENUM_UNKNOWN:
            mapping = self.enum_mappings.get(issue.field, {})
            actual = issue.actual.strip("'\"")
            if actual in mapping:
                issue.convertible = True
                issue.conversion = "回滚前将 %s=%s 批量改写为 %s" % (
                    issue.field, actual, mapping[actual])
            else:
                issue.conversion = "未提供枚举映射，旧版本无法识别该取值"
        elif issue.kind == R.KIND_MISSING_REQUIRED:
            if issue.field in self.new.field_map():
                issue.convertible = True
                issue.conversion = "该字段在新版本中仍存在，属数据异常，可回填默认值"
            else:
                issue.conversion = "新版本已删除该字段，写入的数据永久缺失，无法从现有数据恢复"
        elif issue.kind == R.KIND_TYPE_MISMATCH:
            issue.conversion = "字段类型变化，旧版本反序列化会失败或得到错误值"
        return issue


def verdict_and_steps(issues):
    blocking = [i for i in issues
                if not i.convertible and i.kind != R.KIND_UNKNOWN_FIELD]
    convertible = [i for i in issues
                   if i.convertible and i.kind != R.KIND_UNKNOWN_FIELD]

    steps = []
    if blocking:
        verdict = VERDICT_NO_ROLLBACK
        steps.append("禁止直接回滚：存在旧版本无法读取且无法转换的数据")
        for kind in sorted({i.kind for i in blocking}):
            fields = sorted({i.field for i in blocking if i.kind == kind})
            steps.append("阻断项 %s 涉及字段: %s" % (kind, ", ".join(fields)))
        steps.append("处理步骤: 1) 从回滚前备份恢复受影响数据 "
                     "2) 或让新版本继续双写旧格式直至旧字段数据补齐 "
                     "3) 确认无阻断项后再执行回滚")
    elif convertible:
        verdict = VERDICT_CONVERT
        steps.append("回滚前必须完成以下转换，否则旧版本启动/读取会失败：")
        seen = set()
        for i in convertible:
            if i.conversion and i.conversion not in seen:
                seen.add(i.conversion)
                steps.append("- " + i.conversion)
        steps.append("转换完成后用本工具复跑验证，全部通过方可回滚")
    else:
        verdict = VERDICT_OK
        steps.append("新旧版本读写完全兼容，可直接回滚，无需数据转换")
    return verdict, steps
