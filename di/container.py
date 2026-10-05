"""依赖注入容器：解析顺序、环检测、延迟注入、失败回收。

解析顺序的确定方式（确定性，可复现）：
  1. 只考虑「即时依赖」构成的有向图；延迟依赖不参与拓扑排序。
  2. 从根组件做 DFS，依赖按注册时声明的顺序依次展开；
     每个组件在其全部即时依赖构造完成后才构造（后序遍历）。
  3. 同名组件只构造一次（单例）；图是无环时该顺序是唯一的拓扑序。
  4. 构造前先用 Tarjan SCC 对根可达子图做静态环检测：
     所有环成员一次性报出，不做任何构造（fail-fast）。
  5. 延迟依赖在句柄第一次被取值时才走同一套 DFS 构造；
     若取值发生在构造期且撞上正在构造中的组件，则报动态环。

全部遍历均为迭代实现（显式栈），万级链深不会触发递归深度限制。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .trace import Trace


class DIError(Exception):
    """容器相关错误的基类。"""


class MissingDependencyError(DIError):
    def __init__(self, owner: str, dep: str) -> None:
        super().__init__(f"组件 {owner!r} 依赖了未注册的组件 {dep!r}")
        self.owner = owner
        self.dep = dep


class ConstructionError(DIError):
    """工厂执行抛错。original 为原始异常。"""

    def __init__(self, name: str, original: BaseException) -> None:
        super().__init__(f"构造组件 {name!r} 失败: {original!r}")
        self.name = name
        self.original = original


class CircularDependencyError(DIError):
    """循环依赖。report 为完整环报告（全部环成员 + 每个环的示例路径）。"""

    def __init__(self, report: "CycleReport") -> None:
        super().__init__("检测到循环依赖:\n" + report.render())
        self.report = report


class LazyCycleError(DIError):
    """延迟依赖在构造期被取值，撞上正在构造中的组件（动态环）。"""

    def __init__(self, name: str, active_path: List[str]) -> None:
        cycle = active_path[active_path.index(name):] + [name]
        super().__init__(
            f"延迟依赖在构造期被取值，撞上正在构造的组件，形成动态环: "
            f"{' -> '.join(cycle)}"
        )
        self.cycle = cycle


@dataclass(frozen=True)
class LazyDep:
    """声明一个延迟依赖：构造期只注入句柄，第一次取值时才真正解析。"""
    name: str


def lazy(name: str) -> LazyDep:
    return LazyDep(name)


@dataclass
class Spec:
    name: str
    factory: Callable[..., Any]
    deps: Tuple[str, ...] = ()          # 即时依赖，顺序即解析顺序
    lazy_deps: Tuple[str, ...] = ()     # 延迟依赖
    disposer: Optional[Callable[[Any], None]] = None


class LazyRef:
    """延迟注入句柄。

    语义：构造期注入的是本句柄而不是实例；第一次 .get() 或属性访问时
    才真正解析目标组件（此时才构造目标及其即时依赖子树），之后缓存结果。
    """

    __slots__ = ("_container", "_name", "_resolved", "_value")

    def __init__(self, container: "Container", name: str) -> None:
        object.__setattr__(self, "_container", container)
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_resolved", False)
        object.__setattr__(self, "_value", None)

    @property
    def target_name(self) -> str:
        return self._name

    @property
    def resolved(self) -> bool:
        return self._resolved

    def get(self) -> Any:
        if not self._resolved:
            value = self._container._resolve_lazy(self._name)
            object.__setattr__(self, "_value", value)
            object.__setattr__(self, "_resolved", True)
        return self._value

    def __getattr__(self, item: str) -> Any:
        return getattr(self.get(), item)

    def __repr__(self) -> str:
        state = "resolved" if self._resolved else "pending"
        return f"<LazyRef {self._name} ({state})>"


@dataclass
class CycleInfo:
    members: List[str]        # 环上全部成员（SCC，确定性排序）
    example: List[str]        # 一个具体环路径，首尾相同


@dataclass
class CycleReport:
    cycles: List[CycleInfo]

    @property
    def members(self) -> List[str]:
        seen: List[str] = []
        for c in self.cycles:
            for m in c.members:
                if m not in seen:
                    seen.append(m)
        return seen

    def render(self) -> str:
        lines = [f"共 {len(self.cycles)} 个环，涉及 {len(self.members)} 个组件: "
                 + ", ".join(self.members)]
        for i, c in enumerate(self.cycles, 1):
            lines.append(f"  环 #{i}: 成员[{', '.join(c.members)}]  路径: "
                         + " -> ".join(c.example))
        return "\n".join(lines)


# 组件状态机
PENDING, BUILDING, DONE = 0, 1, 2


class Container:
    def __init__(self, trace: Optional[Trace] = None) -> None:
        self._specs: Dict[str, Spec] = {}
        self._instances: Dict[str, Any] = {}
        self._state: Dict[str, int] = {}
        self._built_order: List[str] = []       # 已构造实例的构造顺序（回收时逆序）
        self._active_path: List[str] = []       # 当前构造调用栈（BUILDING 路径）
        self.trace = trace if trace is not None else Trace()

    # ------------------------------------------------------------------ 注册

    def register(self, name: str, factory: Callable[..., Any],
                 deps: Tuple[Any, ...] = (),
                 disposer: Optional[Callable[[Any], None]] = None) -> None:
        """注册组件。deps 中 str 为即时依赖，lazy('x') 为延迟依赖。"""
        if name in self._specs:
            raise DIError(f"组件 {name!r} 重复注册")
        eager: List[str] = []
        lazy_deps: List[str] = []
        for d in deps:
            if isinstance(d, LazyDep):
                lazy_deps.append(d.name)
            elif isinstance(d, str):
                eager.append(d)
            else:
                raise DIError(f"组件 {name!r} 的依赖必须是 str 或 lazy(...)，得到 {d!r}")
        self._specs[name] = Spec(name, factory, tuple(eager), tuple(lazy_deps), disposer)
        self._state[name] = PENDING

    # ------------------------------------------------------------------ 解析

    def resolve(self, root: str) -> Any:
        """解析根组件：返回实例。失败时回收本次新建的全部实例。"""
        self._check_known(root, "<root>")
        self.trace.reset_clock()
        self.trace.emit("resolve_begin", root, 0, "开始解析")
        self._check_cycles(root)          # fail-fast：静态环在构造前报出
        built_before = len(self._built_order)
        try:
            instance = self._build(root)
        except Exception:
            self._rollback(built_before)   # 回收本次新建实例后原样抛出
            raise
        self.trace.emit("resolve_end", root, 0, "解析完成")
        return instance

    def _build(self, name: str) -> Any:
        """迭代 DFS 后序构造 name 及其即时依赖子树，返回 name 的实例。"""
        if self._state[name] == DONE:
            self.trace.emit("cached", name, len(self._active_path), "复用已构造实例")
            return self._instances[name]
        if self._state[name] == BUILDING:
            raise LazyCycleError(name, list(self._active_path))

        # 显式栈上的帧: (组件名, 已排好序的即时依赖, 下一个待处理下标, 已构造的依赖实例)
        stack: List[List[Any]] = []
        self._enter(name)
        stack.append([name, list(self._specs[name].deps), 0, []])

        while stack:
            frame = stack[-1]
            cur, deps, idx, args = frame[0], frame[1], frame[2], frame[3]
            if idx < len(deps):
                dep = deps[idx]
                frame[2] = idx + 1
                self._check_known(dep, cur)
                st = self._state[dep]
                if st == DONE:
                    self.trace.emit("cached", dep, len(self._active_path),
                                    f"供 {cur} 使用")
                    args.append(self._instances[dep])
                elif st == BUILDING:
                    raise LazyCycleError(dep, list(self._active_path))
                else:
                    self.trace.emit("dep_wait", cur, len(self._active_path),
                                    f"等待即时依赖 {dep}")
                    self._enter(dep)
                    stack.append([dep, list(self._specs[dep].deps), 0, []])
            else:
                stack.pop()
                args.extend(self._make_lazy_args(cur))
                self._construct(cur, args)
                if stack:  # 把构造好的实例回填给父帧（它是父帧的依赖）
                    stack[-1][3].append(self._instances[cur])

        return self._instances[name]

    def _enter(self, name: str) -> None:
        self._state[name] = BUILDING
        self._active_path.append(name)
        self.trace.emit("visit", name, len(self._active_path) - 1)

    def _make_lazy_args(self, name: str) -> List["LazyRef"]:
        args = []
        for ld in self._specs[name].lazy_deps:
            self._check_known(ld, name)
            self.trace.emit("lazy_inject", name, len(self._active_path) - 1,
                            f"注入 {ld} 的延迟句柄（不取值）")
            args.append(LazyRef(self, ld))
        return args

    def _construct(self, name: str, args: List[Any]) -> None:
        depth = len(self._active_path) - 1
        self.trace.emit("construct_begin", name, depth,
                        f"factory({', '.join(self._specs[name].deps)})")
        try:
            instance = self._specs[name].factory(*args)
        except LazyCycleError:
            self.trace.emit("fail", name, depth, "延迟依赖取值形成动态环")
            self._state[name] = PENDING
            self._active_path.pop()
            raise  # 保持动态环异常类型，交给 resolve 统一回收
        except Exception as exc:  # noqa: BLE001 - 包装为 ConstructionError
            self.trace.emit("fail", name, depth, f"工厂抛错: {exc!r}")
            self._state[name] = PENDING
            self._active_path.pop()
            raise ConstructionError(name, exc) from exc
        self._instances[name] = instance
        self._built_order.append(name)
        self._state[name] = DONE
        self._active_path.pop()
        self.trace.emit("construct_end", name, depth, "构造完成")

    # ------------------------------------------------------------ 延迟注入

    def _resolve_lazy(self, name: str) -> Any:
        """延迟句柄第一次取值：此刻才真正构造目标子树。"""
        depth = len(self._active_path)
        if self._state.get(name) == BUILDING:
            raise LazyCycleError(name, list(self._active_path))
        self.trace.emit("lazy_access", name, depth, "延迟句柄第一次取值")
        return self._build(name)

    # ------------------------------------------------------------ 失败回收

    def _rollback(self, built_before: int) -> None:
        """回收本次解析新建的实例：按构造逆序 dispose 并移出容器。"""
        new_built = self._built_order[built_before:]
        for name in reversed(new_built):
            instance = self._instances.pop(name)
            self._state[name] = PENDING
            disposer = self._specs[name].disposer
            if disposer is not None:
                try:
                    disposer(instance)
                    self.trace.emit("dispose", name, 0, "disposer 执行成功")
                except Exception as exc:  # noqa: BLE001 - 回收不中断，继续清理
                    self.trace.emit("dispose", name, 0,
                                    f"disposer 抛错（已忽略，继续回收）: {exc!r}")
            else:
                self.trace.emit("dispose", name, 0, "无 disposer，仅解除引用")
        del self._built_order[built_before:]
        # 构造栈上可能残留 BUILDING 节点（含延迟取值嵌套失败），统一复位
        for n, st in list(self._state.items()):
            if st == BUILDING:
                self._state[n] = PENDING
        self._active_path.clear()

    # ------------------------------------------------------------ 环检测

    def _check_known(self, dep: str, owner: str) -> None:
        if dep not in self._specs:
            raise MissingDependencyError(owner, dep)

    def _eager_graph(self, root: str) -> Dict[str, List[str]]:
        """根可达的即时依赖子图（BFS，确定性顺序）。"""
        graph: Dict[str, List[str]] = {}
        queue = deque([root])
        while queue:
            n = queue.popleft()
            if n in graph:
                continue
            spec = self._specs[n]
            for dep in spec.deps:
                self._check_known(dep, n)
            graph[n] = list(spec.deps)
            queue.extend(spec.deps)
        return graph

    def _check_cycles(self, root: str) -> None:
        graph = self._eager_graph(root)
        sccs = _tarjan_scc(graph)
        cycles: List[CycleInfo] = []
        for scc in sccs:
            is_cycle = len(scc) > 1 or (len(scc) == 1 and scc[0] in graph[scc[0]])
            if is_cycle:
                members = sorted(scc)
                cycles.append(CycleInfo(members, _find_cycle_path(graph, members)))
        if cycles:
            report = CycleReport(cycles)
            self.trace.emit("cycle", root, 0, f"{len(cycles)} 个环: {report.members}")
            raise CircularDependencyError(report)


# ---------------------------------------------------------------- 图算法

def _tarjan_scc(graph: Dict[str, List[str]]) -> List[List[str]]:
    """迭代版 Tarjan 强连通分量，返回按发现顺序的 SCC 列表。"""
    index: Dict[str, int] = {}
    lowlink: Dict[str, int] = {}
    on_stack: Set[str] = set()
    stack: List[str] = []
    sccs: List[List[str]] = []
    counter = 0

    for root in graph:
        if root in index:
            continue
        work: List[Tuple[str, int]] = [(root, 0)]
        while work:
            node, child_idx = work[-1]
            if child_idx == 0:
                index[node] = lowlink[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            recursed = False
            neighbors = graph[node]
            i = child_idx
            while i < len(neighbors):
                nxt = neighbors[i]
                if nxt not in index:
                    work[-1] = (node, i + 1)
                    work.append((nxt, 0))
                    recursed = True
                    break
                if nxt in on_stack:
                    lowlink[node] = min(lowlink[node], index[nxt])
                i += 1
            if recursed:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[node])
            if lowlink[node] == index[node]:
                scc = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    scc.append(w)
                    if w == node:
                        break
                sccs.append(scc)
    return sccs


def _find_cycle_path(graph: Dict[str, List[str]], members: List[str]) -> List[str]:
    """在 SCC 内用 BFS 找一条确定性环路径（成员排序后从最小成员出发）。"""
    member_set = set(members)
    start = members[0]
    if len(members) == 1:                       # 自环
        return [start, start]
    prev: Dict[str, Optional[str]] = {start: None}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for nxt in graph[node]:
            if nxt not in member_set:
                continue
            if nxt == start:                    # 回到起点，拼出环
                path = []
                cur = node
                while cur is not None:
                    path.append(cur)
                    cur = prev[cur]
                path.reverse()
                path.append(start)
                return path
            if nxt not in prev:
                prev[nxt] = node
                queue.append(nxt)
    return members + [members[0]]               # 理论不可达（SCC 必含环）
