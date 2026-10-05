"""资源限制的继承与覆盖（仅 Python 3 标准库）。

核心语义（规则表详见 RULES.md）：
- 每个维度都是“上限型”：数值越小越严格，沿继承链取 min。
- 缺失（None / 未声明）=> 继承父级生效值；根缺失 => 不限（UNLIMITED）。
- 零值是显式声明：0 合法且最严格，与缺失严格区分。
- 子级声明超过父级生效值 => 违约：
    Policy.CLAMP  收敛到父级上限，并在来源链中记录 CLAMP；
    Policy.REJECT 抛出 LimitExceededError，拒绝整棵子树生效。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import inf, isinf
from typing import Dict, Iterator, List, Optional, Tuple

MISSING = None          # 缺失标记：未声明该维度
UNLIMITED = None        # 对外表示：不限（根缺失一路继承到底）

# 全部维度均为“上限型”：越小越严格
DIMENSIONS: Tuple[str, ...] = ("cpu_cores", "memory_mb", "pids", "disk_mb", "net_mbps")


class Policy(Enum):
    CLAMP = "clamp"     # 子级超限 -> 收敛到父级上限
    REJECT = "reject"   # 子级超限 -> 拒绝（抛异常）


class LimitExceededError(ValueError):
    """REJECT 模式下，子级试图突破父级上限时抛出。"""


class Action(Enum):
    ROOT_DEFAULT = "ROOT_DEFAULT"  # 根缺失 -> 不限
    SET = "SET"                    # 显式声明且 <= 父级生效值
    INHERIT = "INHERIT"            # 缺失 -> 继承父级生效值
    CLAMP = "CLAMP"                # 超过父级 -> 收敛到父级上限


@dataclass(frozen=True, slots=True)
class ChainEntry:
    """来源链中的一环：某个层级对某维度的处理记录。"""
    node: str                    # 节点路径
    depth: int                   # 层级（根为 0）
    declared: Optional[float]    # 该层原始声明；None = 缺失
    action: Action               # 该层采取的动作
    value_after: Optional[float] # 经过该层后的生效值；None = 不限


@dataclass(frozen=True, slots=True)
class EffectiveLimit:
    """某节点某维度的最终生效值及完整来源链。"""
    dimension: str
    value: Optional[float]       # 生效值；None = 不限
    source: Optional[str]        # 决定该值的节点路径；None = 根默认不限
    chain: Tuple[ChainEntry, ...]
    clamped: bool                # 本节点是否被收敛
    requested: Optional[float]   # 本节点原始声明；None = 缺失


class Node:
    """资源限制树节点。limits 中 None / 不出现 均表示缺失。"""
    __slots__ = ("name", "limits", "children")

    def __init__(self, name: str, limits: Optional[Dict[str, float]] = None,
                 children: Optional[List["Node"]] = None):
        self.name = name
        self.limits: Dict[str, float] = dict(limits) if limits else {}
        self.children: List[Node] = list(children) if children else []

    def add(self, child: "Node") -> "Node":
        self.children.append(child)
        return child

    def iter_nodes(self) -> Iterator["Node"]:
        stack = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(node.children)


def _check_declared(path: str, dim: str, declared: float) -> None:
    if not isinstance(declared, (int, float)) or isinstance(declared, bool):
        raise TypeError(f"{path}: 维度 {dim} 的声明值必须是数值，得到 {declared!r}")
    if declared < 0:
        raise ValueError(f"{path}: 维度 {dim} 不允许负值，得到 {declared!r}")


def resolve_tree(root: Node, policy: Policy = Policy.CLAMP,
                 dimensions: Tuple[str, ...] = DIMENSIONS
                 ) -> Dict[str, Dict[str, EffectiveLimit]]:
    """自根向下解析整棵树，返回 {节点路径: {维度: EffectiveLimit}}。

    迭代式 DFS（避免深树爆栈），每节点每维度 O(1)，整体 O(节点数 × 维度数)。
    """
    dim_set = set(dimensions)
    for node in root.iter_nodes():
        unknown = set(node.limits) - dim_set
        if unknown:
            raise ValueError(f"/{node.name}: 未知维度 {sorted(unknown)}")

    results: Dict[str, Dict[str, EffectiveLimit]] = {}
    # 状态项: (节点, 路径, 深度, 父状态)；父状态: {维度: (生效值, 来源, 来源链)}
    root_path = "/" + root.name
    stack = [(root, root_path, 0, None)]
    while stack:
        node, path, depth, parent_state = stack.pop()
        effs: Dict[str, EffectiveLimit] = {}
        state: Dict[str, Tuple[float, Optional[str], Tuple[ChainEntry, ...]]] = {}
        for dim in dimensions:
            if parent_state is None:
                p_value, p_source, p_chain = inf, None, ()
            else:
                p_value, p_source, p_chain = parent_state[dim]

            declared = node.limits.get(dim, MISSING)
            clamped = False
            if declared is MISSING:
                action = Action.ROOT_DEFAULT if depth == 0 else Action.INHERIT
                value, source = p_value, p_source
            else:
                _check_declared(path, dim, declared)
                if declared > p_value:
                    if policy is Policy.REJECT:
                        raise LimitExceededError(
                            f"{path}: 维度 {dim} 声明 {declared} 超过父级生效值 "
                            f"{p_value if not isinf(p_value) else 'UNLIMITED'}")
                    action, value, source, clamped = Action.CLAMP, p_value, p_source, True
                else:
                    action, value, source = Action.SET, float(declared), path

            entry = ChainEntry(
                node=path, depth=depth, declared=declared, action=action,
                value_after=None if isinf(value) else value)
            chain = p_chain + (entry,)
            effs[dim] = EffectiveLimit(
                dimension=dim,
                value=None if isinf(value) else value,
                source=source, chain=chain, clamped=clamped, requested=declared)
            state[dim] = (value, source, chain)
        results[path] = effs
        for child in node.children:
            stack.append((child, path + "/" + child.name, depth + 1, state))
    return results


def format_chain(eff: EffectiveLimit) -> str:
    """把来源链渲染成可读文本。"""
    lines = [f"[{eff.dimension}] 生效值 = "
             f"{eff.value if eff.value is not None else 'UNLIMITED'}"
             f"  来源 = {eff.source or '<根默认: 不限>'}"]
    for e in eff.chain:
        declared = "缺失" if e.declared is None else e.declared
        after = e.value_after if e.value_after is not None else "UNLIMITED"
        lines.append(f"  L{e.depth} {e.node}: 声明={declared} "
                     f"动作={e.action.value} 经过后={after}")
    return "\n".join(lines)
