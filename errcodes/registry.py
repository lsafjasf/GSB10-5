"""错误定义加载与冲突检测。

检测以下问题：
- malformed_code      错误码不符合 E AA CC NNN 规则
- area_mismatch       码中的业务域不属于声明模块（跨模块撞码的根源）
- unknown_category    类别为非法的 00
- reserved_serial     使用了 900-999 保留序号
- duplicate_code      同一码重复注册（即使含义相同也不允许）
- code_redefined      同一码被赋予不同错误名（同码异义）
- duplicate_name      同名错误绑定了多个码（通常是拷贝改名遗漏）
"""

import json
import os

from .core import (
    AREA_MAP,
    CATEGORY_NAMES,
    RESERVED_SERIAL_MIN,
    parse_code,
)

SEVERITY_ERROR = "ERROR"
SEVERITY_WARN = "WARN"


class Issue:
    def __init__(self, severity, kind, message, code=None, module=None):
        self.severity = severity
        self.kind = kind
        self.message = message
        self.code = code
        self.module = module

    def as_dict(self):
        return {
            "severity": self.severity,
            "kind": self.kind,
            "code": self.code,
            "module": self.module,
            "message": self.message,
        }


class Registry:
    """所有模块错误定义的合并视图。"""

    def __init__(self, entries, issues):
        # entries: code -> {"def": dict, "modules": [module,...]}
        self.entries = entries
        self.issues = issues

    def all_defs(self):
        return [v["def"] for v in self.entries.values()]

    def has_errors(self):
        return any(i.severity == SEVERITY_ERROR for i in self.issues)

    def as_dict(self):
        return {
            "total_codes": len(self.entries),
            "has_errors": self.has_errors(),
            "issues": [i.as_dict() for i in self.issues],
        }


def _load_module_file(path):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    module = data.get("module", os.path.splitext(os.path.basename(path))[0])
    defs = data.get("errors", [])
    return module, defs


def load_modules(paths):
    """读取各模块 JSON 定义，返回 (module, definition) 序列。"""
    loaded = []
    for path in paths:
        module, defs = _load_module_file(path)
        for definition in defs:
            loaded.append((module, definition))
    return loaded


def _lint_definition(module, definition):
    """对单条定义做规则校验，返回 Issue 列表。"""
    issues = []
    code = definition.get("code", "")
    parsed = parse_code(code)
    if not parsed:
        issues.append(Issue(
            SEVERITY_ERROR, "malformed_code",
            "错误码不符合 E+2位域+2位类别+3位序号 的 8 位规则", code, module))
        return issues

    area, category, serial = parsed
    owned = AREA_MAP.get(module)
    if owned is None:
        issues.append(Issue(
            SEVERITY_ERROR, "area_mismatch",
            "模块 '%s' 未在 AREA_MAP 登记，没有业务域" % module, code, module))
    elif owned != area:
        issues.append(Issue(
            SEVERITY_ERROR, "area_mismatch",
            "码的域 %s 不属于模块 '%s'（该模块只允许域 %s）"
            % (area, module, owned), code, module))

    if category == "00":
        issues.append(Issue(
            SEVERITY_WARN, "unknown_category",
            "类别 00 非法（合法范围 01-99）", code, module))

    if int(serial) >= RESERVED_SERIAL_MIN:
        issues.append(Issue(
            SEVERITY_WARN, "reserved_serial",
            "序号 %s 落在 900-999 保留段，请确认是否为兜底错误" % serial,
            code, module))

    if not definition.get("name"):
        issues.append(Issue(
            SEVERITY_ERROR, "malformed_code",
            "缺少错误名 name", code, module))
    if not definition.get("default_message"):
        issues.append(Issue(
            SEVERITY_WARN, "malformed_code",
            "缺少默认文案 default_message", code, module))
    return issues


def build(loaded):
    """把 (module, definition) 序列合并成 Registry 并检测冲突。"""
    issues = []
    by_code = {}
    by_name = {}

    for module, definition in loaded:
        code = definition.get("code", "")
        name = definition.get("name")
        issues.extend(_lint_definition(module, definition))

        existing = by_code.get(code)
        enriched = dict(definition)
        enriched["module"] = module
        if existing is None:
            by_code[code] = {"def": enriched, "modules": [module]}
        else:
            prev = existing["def"]
            if module not in existing["modules"]:
                existing["modules"].append(module)
            if name and prev.get("name") and name == prev.get("name"):
                issues.append(Issue(
                    SEVERITY_ERROR, "duplicate_code",
                    "码 %s 重复注册（%s 与 %s，错误名同为 %s）"
                    % (code, prev.get("module"), module, name),
                    code, "%s,%s" % (prev.get("module"), module)))
            else:
                issues.append(Issue(
                    SEVERITY_ERROR, "code_redefined",
                    "码 %s 同码异义：'%s/%s' 与 '%s/%s'"
                    % (code, prev.get("module"), prev.get("name"),
                       module, name),
                    code, "%s,%s" % (prev.get("module"), module)))
            # 保留首次注册为权威定义
        if name:
            by_name.setdefault(name, []).append((module, code))

    for name, occurrences in by_name.items():
        codes = {code for _, code in occurrences}
        if len(codes) > 1:
            issues.append(Issue(
                SEVERITY_WARN, "duplicate_name",
                "错误名 '%s' 绑定了多个码：%s"
                % (name, ", ".join(sorted(codes))),
                None, ",".join(sorted({m for m, _ in occurrences}))))

    return Registry(by_code, issues)


def build_from_paths(paths):
    return build(load_modules(paths))
