"""资源限制的继承与覆盖库（仅标准库）。

规则摘要（详见 README.md）：
  R1  所有资源维度均为 min-wins：数值越小越严格，生效值 = 链上所有显式值的最小值。
  R2  缺失（key 不存在或值为 None）≠ 零值（显式 0）：
        - 缺失：本层不表态，原样继承父级生效值；
        - 零值：显式 "配额为 0 / 禁止使用"，是最严值，会沿链向下锁定。
  R3  子级只能把限制调小（raw <= 父生效值），不能调大。
  R4  越限处理两种模式：
        - clamp（默认）：收敛到父级上限，并记录 Violation；
        - reject：抛 LimitExceeded，整棵树解析中止。
  R5  根节点某维度缺失时，默认不限制（+inf）。
"""

from collections import namedtuple

MISSING = None
INF = float("inf")

# 受管维度：全部为 "越小越严"（min-wins）
DIMENSIONS = ("cpu_cores", "memory_mb", "pids", "disk_mb", "net_mbps")

# 单层来源链条目：
#   path   该层节点路径
#   dim    维度
#   raw    该层原始配置（None=缺失）
#   action 该层动作：root-default / inherit / set / zero / clamp
#   value  经过该层后的生效值
ChainEntry = namedtuple("ChainEntry", "path dim raw action value")

# 越限记录：子级请求值超过父级生效上限
Violation = namedtuple("Violation", "path dim requested parent_limit result")


class LimitExceeded(Exception):
    """reject 模式下，子级试图突破父级上限时抛出。"""


class Node:
    """配置树节点。limits 为 {维度: 数值}，未出现或 None 表示缺失（继承）。"""

    __slots__ = ("name", "limits", "children")

    def __init__(self, name, limits=None):
        self.name = name
        self.limits = dict(limits) if limits else {}
        self.children = []

    def add(self, child):
        self.children.append(child)
        return child


def _classify(raw, parent_value, path, dim):
    """把单层配置分类为 (生效值, action)。raw 为该层原始值。"""
    if raw is MISSING:
        return parent_value, "inherit"
    if raw == 0:
        return 0, "zero"
    if raw <= parent_value:
        return raw, "set"
    return parent_value, "clamp"


def resolve_tree(root, mode="clamp", dimensions=DIMENSIONS):
    """迭代解析整棵配置树（避免深递归）。

    返回 (result, violations)：
      result     {节点路径: {维度: (生效值, 来源链 tuple)}}
                 继承层直接复用父级来源链对象（共享不可变 tuple），零额外内存。
      violations clamp 模式下全部越限记录；reject 模式下首次越限即抛错。
    mode 为 "clamp"（收敛并记录）或 "reject"（抛 LimitExceeded）。
    """
    if mode not in ("clamp", "reject"):
        raise ValueError("mode must be 'clamp' or 'reject'")

    violations = []
    result = {}

    # 根层基线：缺失 -> 默认不限（inf）
    root_eff = {}
    for dim in dimensions:
        raw = root.limits.get(dim, MISSING)
        if raw is MISSING:
            val, action = INF, "root-default"
        elif raw == 0:
            val, action = 0, "zero"
        else:
            val, action = raw, "set"
        root_eff[dim] = (val, (ChainEntry(root.name, dim, raw, action, val),))
    result[root.name] = root_eff

    stack = [(root, root.name, root_eff)]
    while stack:
        node, path, parent_eff = stack.pop()
        for child in reversed(node.children):
            cpath = path + "/" + child.name
            child_eff = {}
            for dim in dimensions:
                pval, pchain = parent_eff[dim]
                raw = child.limits.get(dim, MISSING)
                if raw is MISSING:
                    # 缺失：原样继承，来源链整条复用
                    child_eff[dim] = (pval, pchain)
                    continue

                if raw == 0:
                    val, action = 0, "zero"
                elif raw <= pval:
                    val, action = raw, "set"
                else:
                    if mode == "reject":
                        raise LimitExceeded(
                            "node %r dim %s: requested %s exceeds parent limit %s"
                            % (cpath, dim, raw, pval)
                        )
                    violations.append(
                        Violation(cpath, dim, raw, pval, "clamped-to-parent")
                    )
                    val, action = pval, "clamp"

                child_eff[dim] = (
                    val,
                    pchain + (ChainEntry(cpath, dim, raw, action, val),),
                )

            result[cpath] = child_eff
            stack.append((child, cpath, child_eff))

    return result, violations


def explain(root, path, dim):
    """重建某节点某维度从根到该层的完整逐层来源链（含继承层）。

    逐层给出 (层路径, 原始值, 动作, 过层后生效值)，适合人工审计。
    """
    parts = path.split("/")
    node = root
    if node.name != parts[0]:
        raise ValueError("path does not start at root %r" % root.name)

    raw = node.limits.get(dim, MISSING)
    if raw is MISSING:
        val, action = INF, "root-default"
    elif raw == 0:
        val, action = 0, "zero"
    else:
        val, action = raw, "set"
    entries = [ChainEntry(node.name, dim, raw, action, val)]

    cur = node
    for part in parts[1:]:
        nxt = next((c for c in cur.children if c.name == part), None)
        if nxt is None:
            raise ValueError("no node named %r under %r" % (part, cur.name))
        raw = nxt.limits.get(dim, MISSING)
        val, action = _classify(raw, val, cur.name, dim)
        if action == "clamp":
            action = "clamp(%s>%s)" % (raw, val)
        entries.append(ChainEntry("/".join([entries[-1].path, part]), dim, raw, action, val))
        cur = nxt
    return entries


def fmt_value(v):
    return "inf(不限)" if v == INF else str(v)
