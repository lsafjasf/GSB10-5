"""di.py — 轻量依赖注入容器（仅 Python 标准库）。

核心能力：
1. 解析顺序：迭代式 DFS 后序遍历（依赖先于使用者，兄弟按声明顺序）。
2. 循环依赖：Tarjan SCC 定位环上全部成员，并给出一条具体环路径。
3. 延迟注入：LazyRef 占位，首次 get() 时才真正解析（可解开构造期环）。
4. 失败回收：构造中途抛错时，按构造逆序回收本次已建实例。
5. 解析轨迹：Tracer 记录每一步，可完整回放。
"""

from __future__ import annotations


class DIError(Exception):
    """容器相关错误基类。"""


class CycleError(DIError):
    """检测到循环依赖。report 为人类可读的环报告，sccs 为全部环成员分组。"""

    def __init__(self, report: str, sccs: list[list[str]]):
        super().__init__(report)
        self.report = report
        self.sccs = sccs


class ConstructionError(DIError):
    """组件构造失败。cleaned 记录已回收的实例名（回收顺序）。"""

    def __init__(self, name: str, cause: BaseException, cleaned: list[str]):
        super().__init__(f"构造 {name!r} 失败: {cause!r}; 已回收: {cleaned}")
        self.name = name
        self.cause = cause
        self.cleaned = cleaned


class UnknownComponentError(DIError):
    pass


class Tracer:
    """解析轨迹记录器。每条事件带序号，render() 输出完整轨迹。"""

    def __init__(self) -> None:
        self.events: list[tuple[int, str, str]] = []
        self._seq = 0

    def log(self, kind: str, msg: str) -> None:
        self._seq += 1
        self.events.append((self._seq, kind, msg))

    def render(self) -> str:
        return "\n".join(f"[{seq:03d}] {kind:<12} {msg}"
                         for seq, kind, msg in self.events)

    def clear(self) -> None:
        self.events.clear()
        self._seq = 0


class LazyRef:
    """延迟注入句柄。注入时不解析目标，首次 get() 才真正取值并缓存。"""

    def __init__(self, container: "Container", name: str) -> None:
        self._container = container
        self._name = name
        self._resolved = False
        self._value = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def resolved(self) -> bool:
        return self._resolved

    def get(self):
        if not self._resolved:
            self._container.tracer.log(
                "RESOLVE-LAZY", f"LazyRef({self._name}) 首次取值，开始真正解析")
            self._value = self._container.resolve(self._name)
            self._resolved = True
            self._container.tracer.log(
                "LAZY-DONE", f"LazyRef({self._name}) 取值完成并缓存")
        else:
            self._container.tracer.log(
                "LAZY-CACHED", f"LazyRef({self._name}) 命中缓存，直接返回")
        return self._value

    __call__ = get

    def __repr__(self) -> str:
        state = "resolved" if self._resolved else "pending"
        return f"LazyRef({self._name}, {state})"


class _Registration:
    __slots__ = ("name", "factory", "deps", "lazy", "disposer")

    def __init__(self, name, factory, deps, lazy, disposer):
        self.name = name
        self.factory = factory
        self.deps = deps or {}       # 参数名 -> 组件名（急切依赖）
        self.lazy = lazy or {}       # 参数名 -> 组件名（延迟依赖，注入 LazyRef）
        self.disposer = disposer     # 可选回收函数 fn(instance)


class Container:
    """依赖注入容器。单例语义：每个组件只构造一次。"""

    def __init__(self, tracer: Tracer | None = None) -> None:
        self.tracer = tracer if tracer is not None else Tracer()
        self._regs: dict[str, _Registration] = {}
        self._singletons: dict[str, object] = {}

    # ------------------------------------------------------------------ 注册

    def register(self, name, factory, deps=None, lazy=None, disposer=None):
        """注册组件。

        factory(**kwargs)：构造函数；deps 中声明的急切依赖会先被构造，
        其产物按参数名注入；lazy 中声明的依赖注入 LazyRef 占位对象。
        disposer(instance)：可选，失败回收/关闭时调用；缺省则尝试 close()。
        """
        if name in self._regs:
            raise DIError(f"组件重复注册: {name!r}")
        self._regs[name] = _Registration(name, factory, deps, lazy, disposer)
        return self

    # ------------------------------------------------------------- 环检测

    def _reachable(self, roots: list[str]) -> set[str]:
        """从 roots 沿急切依赖边可达的全部组件（迭代式，避免深链爆栈）。"""
        seen: set[str] = set()
        stack = list(roots)
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            if node not in self._regs:
                raise UnknownComponentError(f"未注册的组件: {node!r}")
            seen.add(node)
            stack.extend(self._regs[node].deps.values())
        return seen

    def _tarjan_scc(self, nodes: set[str]) -> list[list[str]]:
        """迭代式 Tarjan，返回全部强连通分量（仅考虑急切依赖边）。"""
        index_of: dict[str, int] = {}
        lowlink: dict[str, int] = {}
        on_stack: set[str] = set()
        stack: list[str] = []
        sccs: list[list[str]] = []
        counter = 0

        for root in nodes:
            if root in index_of:
                continue
            work = [(root, iter(sorted(self._deps_of(root))))]
            index_of[root] = lowlink[root] = counter
            counter += 1
            stack.append(root)
            on_stack.add(root)
            while work:
                node, it = work[-1]
                advanced = False
                for nxt in it:
                    if nxt not in nodes:
                        continue
                    if nxt not in index_of:
                        index_of[nxt] = lowlink[nxt] = counter
                        counter += 1
                        stack.append(nxt)
                        on_stack.add(nxt)
                        work.append((nxt, iter(sorted(self._deps_of(nxt)))))
                        advanced = True
                        break
                    elif nxt in on_stack:
                        lowlink[node] = min(lowlink[node], index_of[nxt])
                if advanced:
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    lowlink[parent] = min(lowlink[parent], lowlink[node])
                if lowlink[node] == index_of[node]:
                    scc = []
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        scc.append(member)
                        if member == node:
                            break
                    sccs.append(sorted(scc))
        return sccs

    def _deps_of(self, name: str) -> list[str]:
        return list(self._regs[name].deps.values())

    def _cycle_path(self, scc: list[str]) -> list[str]:
        """在 SCC 内部找一条具体环路径，如 A -> B -> C -> A。"""
        members = set(scc)
        start = scc[0]
        # 自环
        if len(scc) == 1:
            return [start, start]
        path = [start]
        on_path = {start}
        stack = [(start, iter(sorted(self._deps_of(start))))]
        while stack:
            node, it = stack[-1]
            found = False
            for nxt in it:
                if nxt not in members:
                    continue
                if nxt == start and len(path) >= 1:
                    return path + [start]
                if nxt not in on_path:
                    path.append(nxt)
                    on_path.add(nxt)
                    stack.append((nxt, iter(sorted(self._deps_of(nxt)))))
                    found = True
                    break
            if not found:
                stack.pop()
                if path:
                    on_path.discard(path.pop())
        return path  # 理论上不可达

    def find_cycles(self, roots: list[str] | None = None) -> list[dict]:
        """返回全部环：[{'members': [...], 'path': [...]}]。无环返回 []。"""
        nodes = self._reachable(roots if roots else list(self._regs))
        cycles = []
        for scc in self._tarjan_scc(nodes):
            is_self_loop = (len(scc) == 1
                            and scc[0] in self._deps_of(scc[0]))
            if len(scc) > 1 or is_self_loop:
                cycles.append({"members": scc,
                               "path": self._cycle_path(scc)})
        return cycles

    def cycle_report(self, roots: list[str] | None = None) -> str:
        """人类可读的环报告；无环时返回空串。"""
        cycles = self.find_cycles(roots)
        if not cycles:
            return ""
        lines = [f"检测到 {len(cycles)} 个循环依赖："]
        for i, cyc in enumerate(cycles, 1):
            lines.append(f"  环 #{i}: 成员 {len(cyc['members'])} 个: "
                         f"{', '.join(cyc['members'])}")
            lines.append(f"         环路径: {' -> '.join(cyc['path'])}")
        return "\n".join(lines)

    # ------------------------------------------------------------- 解析顺序

    def resolution_order(self, roots: list[str] | None = None) -> list[str]:
        """计算解析顺序：迭代式 DFS 后序。

        规则：
        - 依赖先于使用者构造（后序）；
        - 兄弟依赖按注册时 deps 字典的声明顺序展开；
        - 多 root 按传入顺序依次展开；
        - 存在环时抛 CycleError（含完整环报告）。
        """
        order_roots = roots if roots else list(self._regs)
        nodes = self._reachable(order_roots)
        cycles = self.find_cycles(order_roots)
        if cycles:
            raise CycleError(self.cycle_report(order_roots),
                             [c["members"] for c in cycles])

        order: list[str] = []
        done: set[str] = set()
        for root in order_roots:
            if root in done:
                continue
            # 迭代式 DFS：栈元素为 (节点, 是否已展开子节点)。
            # 前面 SCC 预检已保证无环，此处只需按 done 去重；
            # 同一依赖若被多个兄弟共享，允许重复入栈（先完成者置 done）。
            stack: list[tuple[str, bool]] = [(root, False)]
            while stack:
                node, expanded = stack.pop()
                if node in done:
                    continue
                if not expanded:
                    stack.append((node, True))
                    for dep in reversed(self._deps_of(node)):
                        if dep not in done:
                            stack.append((dep, False))
                else:
                    done.add(node)
                    order.append(node)
        for pos, name in enumerate(order, 1):
            self.tracer.log("ORDER", f"解析顺序 #{pos}: {name}")
        return order

    # ----------------------------------------------------------------- 解析

    def resolve(self, name: str):
        """解析组件（含其全部急切依赖），返回单例。

        构造中途失败时，按构造逆序回收本次调用新建的实例，
        然后抛 ConstructionError（cleaned 字段为回收清单）。
        """
        if name in self._singletons:
            self.tracer.log("CACHED", f"{name} 命中单例缓存")
            return self._singletons[name]

        order = self.resolution_order([name])
        created: list[str] = []
        try:
            for comp in order:
                if comp in self._singletons:
                    continue
                reg = self._regs[comp]
                self.tracer.log("ENTER", f"开始构造 {comp}")
                kwargs = {}
                for param, dep in reg.deps.items():
                    kwargs[param] = self._singletons[dep]
                    self.tracer.log("INJECT", f"{comp}.{param} <- {dep} (急切)")
                for param, dep in reg.lazy.items():
                    kwargs[param] = LazyRef(self, dep)
                    self.tracer.log(
                        "INJECT-LAZY",
                        f"{comp}.{param} <- LazyRef({dep}) (延迟，未解析)")
                instance = reg.factory(**kwargs)
                self._singletons[comp] = instance
                created.append(comp)
                self.tracer.log("CREATE", f"{comp} 构造完成 -> {instance!r}")
        except BaseException as exc:
            self.tracer.log("FAIL", f"构造 {comp} 失败: {exc!r}，开始回收")
            cleaned = self._cleanup(created)
            raise ConstructionError(comp, exc, cleaned) from exc
        return self._singletons[name]

    def _cleanup(self, created: list[str]) -> list[str]:
        """按构造逆序回收实例：优先 disposer，其次 close()。回收不中断。"""
        cleaned: list[str] = []
        for comp in reversed(created):
            instance = self._singletons.pop(comp, None)
            if instance is None:
                continue
            reg = self._regs[comp]
            try:
                if reg.disposer is not None:
                    reg.disposer(instance)
                elif hasattr(instance, "close"):
                    instance.close()
                self.tracer.log("CLEANUP", f"{comp} 已回收")
            except BaseException as exc:  # 回收失败不掩盖主异常
                self.tracer.log("CLEANUP-FAIL", f"{comp} 回收失败: {exc!r}")
            cleaned.append(comp)
        return cleaned

    def close(self) -> None:
        """关闭容器：按注册逆序回收全部单例。"""
        self._cleanup([n for n in self._singletons])
        self.tracer.log("CLOSE", "容器已关闭")
