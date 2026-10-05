"""对比引擎：对每条参数做等价判定、敏感处理与差异分类。

分类（category）与分类依据：
- intentional          有意差异：命中白名单，或两边值都符合声明的期望值；
- suspected_omission   疑似遗漏：声明应存在却缺失；存在历史基线值却缺失；
                        一边等于基线调优值、另一边退回声明默认值（调优疑似未应用）；
- to_confirm           待确认：确实不同，但没有任何归因依据（白名单 / 期望值 / 基线）
                        能解释，必须人工确认。

值等价（规范化后相等）不计入差异：原文完全一致计入 identical；
仅写法不同计入 equivalent，并记录写法差异原因。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .attribution import ExpectedSpec, MISSING, WhitelistRule
from .normalize import canonical, equivalence_reasons
from .sensitive import MASK, fingerprint, is_sensitive

INTENTIONAL = "intentional"
TO_CONFIRM = "to_confirm"
SUSPECTED_OMISSION = "suspected_omission"

CATEGORY_LABELS = {
    INTENTIONAL: "有意差异",
    TO_CONFIRM: "待确认",
    SUSPECTED_OMISSION: "疑似遗漏",
}
CATEGORY_ORDER = (INTENTIONAL, TO_CONFIRM, SUSPECTED_OMISSION)


@dataclass
class EquivalentItem:
    """原文不同、规范化后等价的参数（不视为差异）。"""

    key: str
    value_a: str
    value_b: str
    reasons: list[str]
    sensitive: bool = False
    fingerprint_a: str | None = None
    fingerprint_b: str | None = None


@dataclass
class DiffItem:
    key: str
    category: str
    attribution: str  # whitelist / expected / baseline / none
    reason: str
    value_a: object = MISSING
    value_b: object = MISSING
    sensitive: bool = False
    fingerprint_a: str | None = None
    fingerprint_b: str | None = None


@dataclass
class Report:
    label_a: str
    label_b: str
    identical_count: int = 0
    equivalent: list[EquivalentItem] = field(default_factory=list)
    diffs: list[DiffItem] = field(default_factory=list)

    def by_category(self, category: str) -> list[DiffItem]:
        return [item for item in self.diffs if item.category == category]

    @property
    def problem_count(self) -> int:
        return len(self.by_category(TO_CONFIRM)) + len(self.by_category(SUSPECTED_OMISSION))

    def to_text(self) -> str:
        from .report import render_text
        return render_text(self)

    def to_dict(self) -> dict:
        from .report import report_to_dict
        return report_to_dict(self)


def _find_rule(key, value_a, value_b, rules) -> WhitelistRule | None:
    for rule in rules:
        if rule.matches(key, value_a, value_b):
            return rule
    return None


def _classify_present(key, value_a, value_b, spec: ExpectedSpec,
                      rules, baseline, label_a, label_b) -> DiffItem:
    # 1) 白名单：显式允许的有意差异
    rule = _find_rule(key, value_a, value_b, rules)
    if rule is not None:
        return DiffItem(key, INTENTIONAL, "whitelist", f"白名单允许：{rule.reason}",
                        value_a, value_b)

    # 2) 声明的期望值：两边都符合各自环境应有的值
    if spec.matches_envs(key, value_a, value_b):
        return DiffItem(key, INTENTIONAL, "expected",
                        f"符合声明的期望值（{key} 按环境应有不同）", value_a, value_b)

    # 3) 历史基线：识别"调优未应用"
    if key in baseline:
        base = baseline[key]
        a_at_base = canonical(value_a) == canonical(base)
        b_at_base = canonical(value_b) == canonical(base)
        if a_at_base != b_at_base:
            drifted_side = label_b if a_at_base else label_a
            drifted_value = value_b if a_at_base else value_a
            default = spec.default_for(key)
            if default is not MISSING and canonical(drifted_value) == canonical(default):
                return DiffItem(key, SUSPECTED_OMISSION, "baseline+expected",
                                f"{drifted_side} 退回声明默认值，历史基线调优疑似未应用",
                                value_a, value_b)
            return DiffItem(key, TO_CONFIRM, "baseline",
                            f"{drifted_side} 偏离历史基线，白名单/期望值未覆盖",
                            value_a, value_b)
        return DiffItem(key, TO_CONFIRM, "baseline",
                        "两边均偏离历史基线，白名单/期望值未覆盖",
                        value_a, value_b)

    # 4) 无任何归因依据
    return DiffItem(key, TO_CONFIRM, "none",
                    "两边值不同，白名单、声明期望值、历史基线均未覆盖", value_a, value_b)


def _classify_missing(key, value_a, value_b, missing_label: str,
                      spec: ExpectedSpec, rules, baseline) -> DiffItem:
    present_side = "a" if value_a is not MISSING else "b"
    present_value = value_a if present_side == "a" else value_b

    def make(category, attribution, reason):
        return DiffItem(key, category, attribution, reason, value_a, value_b)

    for rule in rules:
        if not rule.matches_key(key):
            continue
        expect = rule.expect_a if present_side == "a" else rule.expect_b
        if expect is not None and canonical(present_value) != canonical(expect):
            continue
        if rule.allow_missing:
            return make(INTENTIONAL, "whitelist",
                        f"{missing_label} 允许不配置：{rule.reason}")
        return make(SUSPECTED_OMISSION, "whitelist",
                    f"白名单要求该参数必须配置，但 {missing_label} 缺失：{rule.reason}")

    if spec.declares(key):
        return make(SUSPECTED_OMISSION, "expected",
                    f"声明应存在，但 {missing_label} 缺失")

    if key in baseline:
        return make(SUSPECTED_OMISSION, "baseline",
                    f"历史基线中存在，但 {missing_label} 缺失（疑似未同步）")

    return make(TO_CONFIRM, "none",
                f"仅单环境定义，{missing_label} 缺失，无任何归因依据")


def compare(env_a: dict, env_b: dict, *, expected=None, whitelist=None,
            baseline=None, label_a: str = "env_a", label_b: str = "env_b") -> Report:
    spec = expected if isinstance(expected, ExpectedSpec) else ExpectedSpec(expected)
    rules = [
        rule if isinstance(rule, WhitelistRule) else WhitelistRule.from_dict(rule)
        for rule in (whitelist or [])
    ]
    baseline = baseline or {}
    report = Report(label_a=label_a, label_b=label_b)

    # 键集合 = 两环境参数 ∪ 声明的期望值 ∪ 历史基线，
    # 这样"声明了/基线有但两边都没配"的遗漏也能被发现。
    all_keys = set(env_a) | set(env_b) | set(spec.declared_keys()) | set(baseline)

    for key in sorted(all_keys):
        value_a = env_a.get(key, MISSING)
        value_b = env_b.get(key, MISSING)
        sensitive = (value_a is not MISSING and is_sensitive(key, value_a)) \
            or (value_b is not MISSING and is_sensitive(key, value_b))

        if value_a is MISSING and value_b is MISSING:
            if spec.declares(key):
                item = DiffItem(key, SUSPECTED_OMISSION, "expected",
                                "声明应存在，但两个环境均未配置")
            else:
                item = DiffItem(key, SUSPECTED_OMISSION, "baseline",
                                "历史基线中存在，但两个环境均未配置")
            report.diffs.append(item)
            continue

        if value_a is not MISSING and value_b is not MISSING:
            if canonical(value_a) == canonical(value_b):
                if str(value_a) == str(value_b):
                    report.identical_count += 1
                else:
                    item = EquivalentItem(
                        key, str(value_a), str(value_b),
                        equivalence_reasons(str(value_a), str(value_b)),
                        sensitive=sensitive)
                    if sensitive:
                        item.value_a, item.value_b = MASK, MASK
                        item.fingerprint_a = fingerprint(value_a)
                        item.fingerprint_b = fingerprint(value_b)
                    report.equivalent.append(item)
                continue
            item = _classify_present(key, value_a, value_b, spec, rules,
                                     baseline, label_a, label_b)
        else:
            missing_label = label_b if value_a is not MISSING else label_a
            item = _classify_missing(key, value_a, value_b,
                                     missing_label, spec, rules, baseline)

        item.sensitive = sensitive
        if sensitive:
            item.value_a = MASK if value_a is not MISSING else MISSING
            item.value_b = MASK if value_b is not MISSING else MISSING
            item.fingerprint_a = fingerprint(value_a) if value_a is not MISSING else None
            item.fingerprint_b = fingerprint(value_b) if value_b is not MISSING else None
        report.diffs.append(item)

    return report
