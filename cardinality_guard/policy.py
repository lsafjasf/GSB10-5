"""Merge rules and the guarded merge pipeline.

Three merge rules are provided:

* TRUNCATE  -- keep the top-K most frequent values plus explicitly pinned
  values; everything else collapses to __OTHER__.  Best for skewed labels
  (a few hot tenants/pods dominate).
* NORMALIZE -- replace values matching a dynamic template with the template
  (ORD-882134 -> ORD<digits>, /users/123 -> /users/{id}).  Static values
  pass through unchanged, so real dimensions survive.
* DROP      -- remove the label entirely.  Only legal for labels whose
  values carry no queryable meaning (random ids) -- enforced by guards.

Safety guards (the "must not merge what should stay distinct" requirement):

* protected_labels may never be rewritten or dropped (PolicyError).
* must_distinguish pairs: after merging, the two series of every pair must
  still map to different merged label sets, otherwise PolicyError.
* DROP is refused when the label's selectivity is below
  drop_selectivity_floor (a low-selectivity label is a dimension, not an
  id) unless the caller passes force=True.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .normalize import template_of, normalize_path, extract_channel, template_distribution

__all__ = [
    "PolicyError", "Rule", "Policy", "PlanEntry",
    "merge_labels", "auto_plan", "check_must_distinguish", "validate_policy",
    "OTHER",
]

OTHER = "__OTHER__"


class PolicyError(Exception):
    """Raised when a merge policy violates a safety guard."""


@dataclass
class Rule:
    action: str                     # 'truncate' | 'normalize' | 'drop' | 'keep'
    k: int = 32                     # truncate: number of top values to keep
    pinned: tuple = ()              # truncate: values always kept
    extract: str | None = None      # normalize: 'channel' keeps id prefix
    path_aware: bool = True         # normalize: apply path templating
    force: bool = False             # drop: override selectivity guard


@dataclass
class PlanEntry:
    label: str
    rule: Rule
    reason: str
    distinct_before: float
    est_distinct_after: float


@dataclass
class Policy:
    rules: dict = field(default_factory=dict)       # label -> Rule
    protected_labels: frozenset = frozenset()
    must_distinguish: tuple = ()                    # ((labelsA, labelsB), ...)
    drop_selectivity_floor: float = 0.9


def _apply_rule(label: str, value: str, rule: Rule, top_values: set) -> str | None:
    """Map one value; returns None when the label is dropped."""
    if rule.action == "drop":
        return None
    if rule.action == "keep":
        return value
    if rule.action == "truncate":
        if value in rule.pinned or value in top_values:
            return value
        return OTHER
    if rule.action == "normalize":
        if rule.extract == "channel":
            ch = extract_channel(value)
            if ch is not None:
                return ch
        if rule.path_aware:
            p = normalize_path(value)
            if p is not None:
                return p
        t = template_of(value)
        return t if t is not None else value
    raise PolicyError(f"unknown action {rule.action!r}")


def merge_labels(labels: dict, policy: Policy, top_values: dict | None = None) -> dict:
    """Apply a policy to one series' labels and return the merged mapping."""
    top_values = top_values or {}
    out = {}
    for name, value in labels.items():
        if name in policy.protected_labels:
            out[name] = value
            continue
        rule = policy.rules.get(name)
        if rule is None:
            out[name] = value
            continue
        merged = _apply_rule(name, value, rule, top_values.get(name, set()))
        if merged is not None:
            out[name] = merged
    return out


def check_must_distinguish(policy: Policy, top_values: dict | None = None) -> list:
    """Verify every must_distinguish pair still differs after merging.

    Returns the list of violating pairs (empty means safe).
    """
    bad = []
    for a, b in policy.must_distinguish:
        if merge_labels(a, policy, top_values) == merge_labels(b, policy, top_values):
            bad.append((a, b))
    return bad


def validate_policy(policy: Policy, report=None, top_values: dict | None = None) -> None:
    """Run all static guards; raise PolicyError on the first violation."""
    for name, rule in policy.rules.items():
        if name in policy.protected_labels and rule.action != "keep":
            raise PolicyError(f"受保护标签 {name!r} 不允许配置归并规则")
        if rule.action == "drop" and not rule.force:
            info = report.labels.get(name) if report else None
            if info is not None and info.selectivity < policy.drop_selectivity_floor:
                raise PolicyError(
                    f"标签 {name!r} 选择率 {info.selectivity:.3f} < "
                    f"{policy.drop_selectivity_floor}，是有区分语义的维度，"
                    f"禁止 drop（可用 force=True 强制）")
    bad = check_must_distinguish(policy, top_values)
    if bad:
        raise PolicyError(
            f"must_distinguish 校验失败，归并会合并本应区分的序列: {bad!r}")


def auto_plan(report, sketches: dict | None = None, protected_labels=(),
              top_k: int = 32, drop_selectivity_floor: float = 0.9) -> list:
    """Derive a merge plan from a DetectionReport.

    Decision table (documented in README):
      * protected label                        -> keep
      * >=90% of values match >=2 templates    -> normalize (keeps structure)
      * selectivity >= floor and entropy >= .9 -> drop (random id)
      * skewed value distribution              -> truncate top-K
      * otherwise                              -> keep + warn
    """
    plan = []
    protected = set(protected_labels)
    for name in report.high_cardinality_labels:
        info = report.labels[name]
        if name in protected:
            plan.append(PlanEntry(name, Rule("keep"),
                                  "受保护的业务维度，禁止归并",
                                  info.distinct, info.distinct))
            continue
        # 1) template-normalisable labels with meaningful structure
        sample = sketches[name].reservoir if sketches and name in sketches else []
        dist = template_distribution(sample)
        total = sum(dist.values()) or 1
        static_ratio = dist.get("static", 0) / total
        if sample and static_ratio <= 0.1 and 2 <= len(dist) <= max(8, top_k):
            extract = "channel" if any(
                t.endswith("<digits>") for t in dist) else None
            plan.append(PlanEntry(
                name, Rule("normalize", extract=extract),
                f"{1 - static_ratio:.0%} 取值匹配动态模板（{len(dist)} 种模板），"
                f"归一化后保留结构语义",
                info.distinct, float(len(dist))))
            continue
        # 2) random-id labels: extremely uniform + unique -> drop
        if info.selectivity >= drop_selectivity_floor and info.entropy_norm >= 0.9:
            plan.append(PlanEntry(
                name, Rule("drop"),
                f"选择率 {info.selectivity:.3f} 且分布均匀（熵 {info.entropy_norm:.3f}），"
                f"取值无查询语义，建议 drop",
                info.distinct, 0.0))
            continue
        # 3) skewed labels -> truncate
        top = info.top_values
        if top:
            top_mass = sum(c for _, c in top[:top_k]) / max(info.total, 1)
            if top_mass >= 0.5 or info.distinct > 10 * top_k:
                est_after = min(top_k, info.distinct) + 1
                plan.append(PlanEntry(
                    name, Rule("truncate", k=top_k),
                    f"取值分布倾斜（top{top_k} 覆盖 {top_mass:.0%}），"
                    f"截断保留热值，其余归入 {OTHER}",
                    info.distinct, float(est_after)))
                continue
        plan.append(PlanEntry(
            name, Rule("keep"),
            "高基数但无安全归并方案，保留并告警", info.distinct, info.distinct))
    return plan
