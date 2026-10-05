#!/usr/bin/env python3
"""插件版本兼容性检查库（仅依赖 Python 3 标准库）。

兼容性判定不看版本号，只对比两份接口/语义清单（manifest）。

清单中的每个插件函数描述四类契约：
  params  - 参数：名称、类型、是否必填、默认值（语义）
  returns - 返回值类型
  raises  - 错误语义：会抛出的错误名及其触发条件
另对比函数集合本身（新增 / 删除函数）。

判定结果三分类：
  COMPATIBLE           兼容：契约逐字一致
  BACKWARD_COMPATIBLE  向后兼容：旧调用方无需改动即可使用新版本（纯放宽/新增）
  INCOMPATIBLE         不兼容：存在旧调用方可能踩中的契约或语义破坏
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

COMPATIBLE = "compatible"
BACKWARD_COMPATIBLE = "backward_compatible"
INCOMPATIBLE = "incompatible"

_NO_DEFAULT = object()


@dataclass(frozen=True)
class Finding:
    """单条差异。kind 描述差异类别，severity 为三分类常量。"""

    kind: str
    severity: str
    location: str
    detail: str

    def to_dict(self) -> Dict[str, str]:
        return {"kind": self.kind, "severity": self.severity,
                "location": self.location, "detail": self.detail}


@dataclass
class Report:
    verdict: str
    findings: List[Finding] = field(default_factory=list)

    @property
    def incompatible_findings(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == INCOMPATIBLE]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verdict": self.verdict,
            "incompatible_count": len(self.incompatible_findings),
            "finding_count": len(self.findings),
            "findings": [f.to_dict() for f in self.findings],
        }


def load_manifest(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _default_of(param: Dict[str, Any]) -> object:
    if "default" in param:
        return param["default"]
    return _NO_DEFAULT


def _param_key(param: Tuple[str, Dict[str, Any]]) -> str:
    return param[0]


def _compare_function(name: str, old: Dict[str, Any], new: Dict[str, Any]) -> List[Finding]:
    findings: List[Finding] = []
    loc = f"function:{name}"

    old_params: Dict[str, Dict[str, Any]] = old.get("params", {}) or {}
    new_params: Dict[str, Dict[str, Any]] = new.get("params", {}) or {}

    for pname in sorted(set(old_params) | set(new_params)):
        ploc = f"{loc}.param:{pname}"
        if pname not in new_params:
            findings.append(Finding(
                "param_removed", INCOMPATIBLE, ploc,
                f"参数 '{pname}' 被删除，旧调用方传入该参数会失败"))
            continue
        if pname not in old_params:
            if new_params[pname].get("required", False):
                findings.append(Finding(
                    "required_param_added", INCOMPATIBLE, ploc,
                    f"新增必填参数 '{pname}'，旧调用方不传会失败"))
            else:
                findings.append(Finding(
                    "optional_param_added", BACKWARD_COMPATIBLE, ploc,
                    f"新增可选参数 '{pname}'（默认值 {new_params[pname].get('default')!r}）"))
            continue

        op, np = old_params[pname], new_params[pname]
        if op.get("type") != np.get("type"):
            findings.append(Finding(
                "param_type_changed", INCOMPATIBLE, ploc,
                f"参数类型 {op.get('type')!r} -> {np.get('type')!r}，旧值可能被拒绝"))
        old_req, new_req = op.get("required", False), np.get("required", False)
        required_changed = old_req != new_req
        if not old_req and new_req:
            findings.append(Finding(
                "param_became_required", INCOMPATIBLE, ploc,
                "可选参数变为必填，省略该参数的旧调用会失败"))
        elif old_req and not new_req:
            findings.append(Finding(
                "param_became_optional", BACKWARD_COMPATIBLE, ploc,
                "必填参数变为可选（含新增默认值），是对旧调用方的放宽"))

        # 必填性已变化时默认值的增删只是配套变化，语义影响已由上面的条目覆盖
        od, nd = _default_of(op), _default_of(np)
        if not required_changed and od is not nd and repr(od) != repr(nd):
            if od is _NO_DEFAULT:
                detail = f"参数新增默认值 {nd!r}，省略该参数时行为改变"
            elif nd is _NO_DEFAULT:
                detail = f"参数默认值 {od!r} 被移除，省略该参数时行为改变"
            else:
                detail = f"默认值 {od!r} -> {nd!r}，省略该参数的旧调用得到不同结果"
            findings.append(Finding(
                "default_changed", INCOMPATIBLE, ploc, detail))

    old_ret = (old.get("returns") or {}).get("type")
    new_ret = (new.get("returns") or {}).get("type")
    if old_ret != new_ret:
        findings.append(Finding(
            "return_type_changed", INCOMPATIBLE, f"{loc}.returns",
            f"返回类型 {old_ret!r} -> {new_ret!r}，旧调用方对返回值的假设失效"))

    old_err: Dict[str, str] = old.get("raises", {}) or {}
    new_err: Dict[str, str] = new.get("raises", {}) or {}
    for ename in sorted(set(old_err) | set(new_err)):
        eloc = f"{loc}.raises:{ename}"
        if ename not in new_err:
            findings.append(Finding(
                "error_removed", INCOMPATIBLE, eloc,
                f"不再抛出错误 '{ename}'（旧条件：{old_err[ename]!r}），依赖捕获该错误的旧调用方失去保护"))
        elif ename not in old_err:
            findings.append(Finding(
                "error_added", INCOMPATIBLE, eloc,
                f"新增抛出错误 '{ename}'（条件：{new_err[ename]!r}），旧调用方未预期该错误"))
        elif old_err[ename] != new_err[ename]:
            findings.append(Finding(
                "error_condition_changed", INCOMPATIBLE, eloc,
                f"错误 '{ename}' 的触发条件 {old_err[ename]!r} -> {new_err[ename]!r}"))

    return findings


def compare_manifests(old: Dict[str, Any], new: Dict[str, Any]) -> Report:
    """对比旧、新两份清单，返回确定性（排序后）的判定报告。"""
    old_funcs: Dict[str, Dict[str, Any]] = old.get("functions", {}) or {}
    new_funcs: Dict[str, Dict[str, Any]] = new.get("functions", {}) or {}

    findings: List[Finding] = []
    for fname in sorted(set(old_funcs) | set(new_funcs)):
        if fname not in new_funcs:
            findings.append(Finding(
                "function_removed", INCOMPATIBLE, f"function:{fname}",
                f"函数 '{fname}' 被删除，旧调用方调用会直接失败"))
        elif fname not in old_funcs:
            findings.append(Finding(
                "function_added", BACKWARD_COMPATIBLE, f"function:{fname}",
                f"新增函数 '{fname}'，不影响旧调用方"))
        else:
            findings.extend(_compare_function(fname, old_funcs[fname], new_funcs[fname]))

    severities = {f.severity for f in findings}
    if INCOMPATIBLE in severities:
        verdict = INCOMPATIBLE
    elif BACKWARD_COMPATIBLE in severities:
        verdict = BACKWARD_COMPATIBLE
    else:
        verdict = COMPATIBLE
    return Report(verdict=verdict, findings=findings)


def report_json(report: Report) -> str:
    """规范 JSON：键排序、无环境相关内容，保证重复检查字节级一致。"""
    return json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True, indent=2)


def _main(argv: List[str]) -> int:
    if len(argv) != 3:
        print("用法: python3 compat_checker.py <旧版本manifest.json> <新版本manifest.json>",
              file=sys.stderr)
        return 2
    report = compare_manifests(load_manifest(argv[1]), load_manifest(argv[2]))
    print(report_json(report))
    return 1 if report.verdict == INCOMPATIBLE else 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
