"""envdiff - 两套环境参数差异对比库（仅标准库）。

归因来源（优先级从高到低）：
  1. 声明的期望值 (expected): 每个环境各自应有的值，两边都符合声明 => 有意差异
  2. 允许差异白名单 (whitelist): 命中规则 => 有意差异
  3. 历史基线 (baseline): 基线中已存在同样的差异 => 待确认（历史遗留，未声明）
                          基线中一致或无记录 => 疑似遗漏

分类：
  EQUAL            完全一致
  EQUIVALENT       值等价但写法不同（大小写/空格/单位/列表顺序）
  INTENTIONAL      有意差异（命中声明期望值或白名单）
  NEEDS_CONFIRM    待确认（基线中已存在该差异，但无声明/白名单依据）
  SUSPECTED_MISS   疑似遗漏（基线一致或无记录，当前出现未声明差异；或参数缺失）

敏感参数（密钥/证书等）：报告中不输出明文，只输出 SHA-256 摘要前缀与是否一致。
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------- 分类常量

EQUAL = "equal"
EQUIVALENT = "equivalent"
INTENTIONAL = "intentional"
NEEDS_CONFIRM = "needs_confirm"
SUSPECTED_MISS = "suspected_miss"

CATEGORY_LABELS = {
    EQUAL: "完全一致",
    EQUIVALENT: "写法不同但等价",
    INTENTIONAL: "有意差异",
    NEEDS_CONFIRM: "待确认",
    SUSPECTED_MISS: "疑似遗漏",
}

# ---------------------------------------------------------------- 敏感参数

SENSITIVE_KEY_PATTERNS = [
    "*password*", "*passwd*", "*secret*", "*token*", "*apikey*", "*api_key*",
    "*privatekey*", "*private_key*", "*credential*", "*cert*", "*.key",
    "*keystore*", "*truststore*",
]


def is_sensitive_key(key: str, extra_patterns: Optional[List[str]] = None) -> bool:
    k = key.lower()
    patterns = SENSITIVE_KEY_PATTERNS + [p.lower() for p in (extra_patterns or [])]
    return any(fnmatch.fnmatch(k, p) for p in patterns)


def fingerprint(value: str) -> str:
    """敏感值指纹：SHA-256 前 12 位，绝不输出明文。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- 值归一化

_UNIT_TABLE = {
    # 存储单位（二进制）
    "b": 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3, "tb": 1024 ** 4,
    "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4,
    # 时间单位
    "ms": 1, "s": 1000, "sec": 1000, "min": 60_000, "h": 3_600_000,
}
_UNIT_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*([a-zA-Z]+)\s*$")
_LIST_SEP_RE = re.compile(r"[,;]")


def _normalize_scalar(text: str, case_sensitive: bool) -> str:
    """标量归一化：去首尾空白、压缩内部空白、单位换算、大小写折叠。"""
    s = re.sub(r"\s+", " ", text.strip())
    m = _UNIT_RE.match(s)
    if m and m.group(2).lower() in _UNIT_TABLE:
        num = float(m.group(1)) * _UNIT_TABLE[m.group(2).lower()]
        s = repr(int(num)) if num == int(num) else repr(num)
    return s if case_sensitive else s.lower()


def normalize_value(value: str, case_sensitive: bool = False) -> Tuple[str, bool]:
    """归一化一个参数值。

    返回 (归一化结果, 是否为列表)。列表按逗号/分号切分、逐项归一化后排序，
    因此列表顺序不同不会产生差异。
    """
    if _LIST_SEP_RE.search(value):
        items = [_normalize_scalar(p, case_sensitive)
                 for p in _LIST_SEP_RE.split(value) if p.strip()]
        return ("\x1f".join(sorted(items)), True)
    return (_normalize_scalar(value, case_sensitive), False)


def values_equivalent(a: str, b: str, case_sensitive: bool = False) -> bool:
    na, _ = normalize_value(a, case_sensitive)
    nb, _ = normalize_value(b, case_sensitive)
    return na == nb


# ---------------------------------------------------------------- 输入解析

def load_env_file(path: str) -> Dict[str, str]:
    """加载参数文件，支持 key=value / key: value（.properties/.env/.yaml 风格）与 JSON。"""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    stripped = text.lstrip()
    if stripped.startswith("{"):
        data = json.loads(text)
        return {str(k): (v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
                for k, v in data.items()}
    params: Dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "//", ";")):
            continue
        m = re.match(r"^([^=:\s]+)\s*[=:]\s*(.*)$", line)
        if m:
            params[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return params


def load_json_file(path: Optional[str]) -> dict:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 归因规则匹配

@dataclass
class WhitelistRule:
    """白名单规则：key 为 glob 模式；values 可选，限定允许出现的值对。"""
    key: str
    reason: str = ""
    values: Optional[List[List[str]]] = None  # [[envA值, envB值], ...]

    def matches(self, key: str, va: Optional[str], vb: Optional[str]) -> bool:
        if not fnmatch.fnmatch(key.lower(), self.key.lower()):
            return False
        if self.values is None:
            return True
        if va is None or vb is None:
            return False
        for pair in self.values:
            if len(pair) == 2 and values_equivalent(pair[0], va) and values_equivalent(pair[1], vb):
                return True
        return False


def parse_whitelist(data: list) -> List[WhitelistRule]:
    rules = []
    for item in data:
        rules.append(WhitelistRule(
            key=item["key"],
            reason=item.get("reason", ""),
            values=item.get("values"),
        ))
    return rules


# ---------------------------------------------------------------- 对比结果

@dataclass
class DiffItem:
    key: str
    category: str
    reason: str
    sensitive: bool = False
    value_a: Optional[str] = None   # 敏感参数时为指纹
    value_b: Optional[str] = None
    normalized_equal: bool = False

    def to_dict(self) -> dict:
        d = {"key": self.key, "category": self.category,
             "category_label": CATEGORY_LABELS[self.category], "reason": self.reason}
        if self.sensitive:
            d["sensitive"] = True
        if self.value_a is not None or self.value_b is not None:
            d["value_a"] = self.value_a
            d["value_b"] = self.value_b
        return d


@dataclass
class DiffReport:
    items: List[DiffItem] = field(default_factory=list)

    def by_category(self, category: str) -> List[DiffItem]:
        return [i for i in self.items if i.category == category]

    def summary(self) -> Dict[str, int]:
        counts = {c: 0 for c in CATEGORY_LABELS}
        for i in self.items:
            counts[i.category] += 1
        return counts

    def to_dict(self) -> dict:
        return {"summary": self.summary(),
                "items": [i.to_dict() for i in self.items]}


# ---------------------------------------------------------------- 核心对比

def _display(key: str, value: Optional[str], sensitive: bool) -> Optional[str]:
    if value is None:
        return None
    return fingerprint(value) if sensitive else value


def compare_envs(env_a: Dict[str, str],
                 env_b: Dict[str, str],
                 expected: Optional[dict] = None,
                 whitelist: Optional[List[WhitelistRule]] = None,
                 baseline: Optional[dict] = None,
                 case_sensitive_keys: Optional[List[str]] = None,
                 extra_sensitive_patterns: Optional[List[str]] = None) -> DiffReport:
    """对比两套环境参数。

    expected:  {"key": {"env_a": "...", "env_b": "...", "reason": "..."}}
    whitelist: [WhitelistRule, ...]
    baseline:  {"key": {"env_a": "...", "env_b": "..."}}  历史基线快照
    """
    expected = expected or {}
    whitelist = whitelist or []
    baseline = baseline or {}
    cs_patterns = [p.lower() for p in (case_sensitive_keys or [])]

    report = DiffReport()
    for key in sorted(set(env_a) | set(env_b)):
        sensitive = is_sensitive_key(key, extra_sensitive_patterns)
        case_sensitive = sensitive or any(fnmatch.fnmatch(key.lower(), p) for p in cs_patterns)
        raw_a, raw_b = env_a.get(key), env_b.get(key)
        va = _display(key, raw_a, sensitive)
        vb = _display(key, raw_b, sensitive)

        # 1) 缺失
        if raw_a is None or raw_b is None:
            rule = next((r for r in whitelist if r.matches(key, raw_a, raw_b)), None)
            if rule:
                report.items.append(DiffItem(
                    key, INTENTIONAL, f"参数缺失，命中白名单: {rule.reason or rule.key}",
                    sensitive, va, vb))
            else:
                missing_in = "env_a" if raw_a is None else "env_b"
                report.items.append(DiffItem(
                    key, SUSPECTED_MISS,
                    f"参数在 {missing_in} 中缺失，且不在白名单，疑似遗漏",
                    sensitive, va, vb))
            continue

        # 2) 完全一致
        if raw_a == raw_b:
            report.items.append(DiffItem(key, EQUAL, "完全一致", sensitive, va, vb))
            continue

        # 3) 写法不同但等价
        if values_equivalent(raw_a, raw_b, case_sensitive):
            report.items.append(DiffItem(
                key, EQUIVALENT,
                "值等价但写法不同（大小写/空格/单位/列表顺序），不计为差异",
                sensitive, va, vb, normalized_equal=True))
            continue

        # 4) 声明的期望值
        exp = expected.get(key)
        if exp is not None:
            ok_a = values_equivalent(raw_a, str(exp.get("env_a", "")), case_sensitive)
            ok_b = values_equivalent(raw_b, str(exp.get("env_b", "")), case_sensitive)
            if ok_a and ok_b:
                report.items.append(DiffItem(
                    key, INTENTIONAL,
                    f"符合声明的期望值: {exp.get('reason', '已声明')}",
                    sensitive, va, vb))
                continue
            report.items.append(DiffItem(
                key, SUSPECTED_MISS,
                "实际值与声明的期望值不符，疑似未按声明同步",
                sensitive, va, vb))
            continue

        # 5) 白名单
        rule = next((r for r in whitelist if r.matches(key, raw_a, raw_b)), None)
        if rule:
            report.items.append(DiffItem(
                key, INTENTIONAL,
                f"命中允许差异白名单: {rule.reason or rule.key}",
                sensitive, va, vb))
            continue

        # 6) 历史基线
        base = baseline.get(key)
        if base is not None:
            b_same = values_equivalent(str(base.get("env_a", "")), str(base.get("env_b", "")))
            if b_same:
                report.items.append(DiffItem(
                    key, SUSPECTED_MISS,
                    "历史基线中两边一致，当前出现未声明差异，疑似遗漏",
                    sensitive, va, vb))
            else:
                report.items.append(DiffItem(
                    key, NEEDS_CONFIRM,
                    "历史基线中已存在该差异，但无声明/白名单依据，待确认",
                    sensitive, va, vb))
            continue

        # 7) 无任何归因依据
        report.items.append(DiffItem(
            key, SUSPECTED_MISS,
            "无声明/白名单/基线记录的新增差异，疑似遗漏",
            sensitive, va, vb))

    return report


# ---------------------------------------------------------------- 报告输出

def render_markdown(report: DiffReport, name_a: str = "env_a", name_b: str = "env_b") -> str:
    lines = ["# 环境参数差异对比报告", "",
             f"对比对象: `{name_a}` vs `{name_b}`", "", "## 汇总", "",
             "| 分类 | 数量 |", "| --- | --- |"]
    summary = report.summary()
    for cat, label in CATEGORY_LABELS.items():
        lines.append(f"| {label} | {summary[cat]} |")
    lines.append("")

    for cat in (SUSPECTED_MISS, NEEDS_CONFIRM, INTENTIONAL, EQUIVALENT):
        items = report.by_category(cat)
        if not items:
            continue
        lines += [f"## {CATEGORY_LABELS[cat]}（{len(items)} 项）", ""]
        for it in items:
            lines.append(f"- `{it.key}` — {it.reason}")
            if it.value_a is not None or it.value_b is not None:
                tag = "（SHA-256 指纹）" if it.sensitive else ""
                lines.append(f"  - {name_a}: `{it.value_a}`{tag}")
                lines.append(f"  - {name_b}: `{it.value_b}`{tag}")
        lines.append("")
    eq = report.by_category(EQUAL)
    lines.append(f"## 完全一致（{len(eq)} 项，从略）")
    lines.append("")
    return "\n".join(lines)
