# -*- coding: utf-8 -*-
"""配置变更影响面分析库（Python 3 标准库实现）。

引用关系模型
------------
1. 静态引用 STATIC
   代码 / 服务中以字面量方式直接读取某个配置项，例如
   ``cfg.get("order.pay_timeout")``。解析时键名完整可知，直接建立
   “配置项 -> 消费方(服务/模块)” 的边。

2. 间接引用 DERIVE
   配置项之间的值派生关系，例如配置 A 的值写成 ``${B} + 100``，
   或 A 的默认值表达式引用 B。建立 “B -> A” 的传导边：B 变更沿
   派生链 A1 -> A2 ... 逐层传播，链上每个配置的消费方都是间接受
   影响方。

3. 动态拼接引用 DYNAMIC
   键名在运行时拼接，例如 ``cfg.get("db." + env + ".host")``，
   静态分析拿不到 ``env`` 的实际取值。此类引用归一化为 glob
   模式（``db.*.host``）；若被变更的配置项命中模式，只能列入
   “待人工确认”，绝不并入已确定清单，也不丢弃。

身份与同名不同义
----------------
配置项身份 = (namespace, name) 二元组。``payment:timeout`` 与
``search:timeout`` 是两个不同节点，任何匹配都要求命名空间完全相等，
因此同名键不会被误判为同一个。

覆盖边界
--------
- 覆盖：字面量读取、跨配置派生链（含环，按访问集去重）、
  同命名空间内的 glob 形态动态拼接、namespace=="*" 的显式跨空间引用。
- 不覆盖（会落入待人工确认而非漏报）：运行时反射、来自外部输入/网络
  的键名、编译期无法看到的动态装载插件内引用；这些应在采集阶段登记为
  DYNAMIC 引用，分析器保证它们出现在待确认清单中。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatchcase

__all__ = [
    "RefKind",
    "ConfigKey",
    "ImpactReport",
    "ImpactedItem",
    "PendingItem",
    "ImpactAnalyzer",
]


class RefKind(Enum):
    STATIC = "static"
    DERIVE = "derive"
    DYNAMIC = "dynamic"


@dataclass(frozen=True)
class ConfigKey:
    """配置项的全局唯一身份：命名空间 + 键名。"""

    namespace: str
    name: str

    def fqn(self) -> str:
        return f"{self.namespace}:{self.name}"


@dataclass(frozen=True)
class ImpactedItem:
    """一个已确定受影响的消费方及其推导路径。"""

    consumer: str
    layer: str          # "direct" 或 "indirect"
    via: ConfigKey      # 该消费方直接读取的配置项
    path: tuple         # 从被变更项到 via 的配置项 FQN 链
    ref_source: str     # 引用证据（文件/表达式等）


@dataclass(frozen=True)
class PendingItem:
    """无法静态判定、需要人工确认的动态引用。"""

    consumer: str
    namespace: str
    pattern: str        # 归一化 glob 模式
    expression: str     # 原始拼接表达式
    matched: ConfigKey  # 命中该模式的被变更配置项
    ref_source: str


@dataclass
class ImpactReport:
    changed: ConfigKey
    direct: list = field(default_factory=list)
    indirect: list = field(default_factory=list)
    pending: list = field(default_factory=list)

    def affected_consumers(self):
        return {item.consumer for item in self.direct} | {
            item.consumer for item in self.indirect
        }

    def pending_consumers(self):
        return {item.consumer for item in self.pending}


class ImpactAnalyzer:
    """配置项引用图与影响面分析器。

    图结构（均为邻接表，内存 O(V+E)）：
      _readers[k]      : 直接读取配置项 k 的静态消费方
      _dependents[k]   : 值派生自 k 的配置项（k 变更会传导过去）
      _dynamic[ns]     : 命名空间 ns 下的动态拼接引用列表
    """

    WILDCARD_NS = "*"

    def __init__(self):
        self._configs = {}          # fqn -> ConfigKey
        self._readers = {}          # fqn -> [(consumer, source)]
        self._dependents = {}       # fqn -> [dep_fqn]
        self._dynamic = {}          # ns -> [(consumer, pattern, expr, source)]
        self._n_static = 0
        self._n_derive = 0
        self._n_dynamic = 0

    # ---------- 数据登记 ----------

    def add_config(self, namespace, name):
        key = ConfigKey(namespace, name)
        self._configs.setdefault(key.fqn(), key)
        return key

    def add_static_ref(self, consumer, namespace, name, source=""):
        """登记静态引用：consumer 直接读取 namespace:name。"""
        fqn = ConfigKey(namespace, name).fqn()
        self._readers.setdefault(fqn, []).append((consumer, source))
        self._n_static += 1

    def add_derive_ref(self, src_namespace, src_name,
                       dst_namespace, dst_name, source=""):
        """登记间接引用：dst 配置项的值派生自 src 配置项。"""
        src = ConfigKey(src_namespace, src_name).fqn()
        dst = ConfigKey(dst_namespace, dst_name).fqn()
        self._configs.setdefault(src, ConfigKey(src_namespace, src_name))
        self._configs.setdefault(dst, ConfigKey(dst_namespace, dst_name))
        self._dependents.setdefault(src, []).append(dst)
        self._n_derive += 1

    def add_dynamic_ref(self, consumer, namespace, pattern,
                        expression=None, source=""):
        """登记动态拼接引用。

        pattern 为归一化 glob（``*`` 匹配任意单段、``**`` 不使用，
        动态拼接一律视为单段未知量），例如
        ``"db." + env + ".host"`` -> ``"db.*.host"``。
        namespace 传 ``"*"`` 表示跨命名空间读取（需人工确认）。
        """
        self._dynamic.setdefault(namespace, []).append(
            (consumer, pattern, expression or pattern, source)
        )
        self._n_dynamic += 1

    # ---------- 基本统计 ----------

    def stats(self):
        return {
            "configs": len(self._configs),
            "static_refs": self._n_static,
            "derive_refs": self._n_derive,
            "dynamic_refs": self._n_dynamic,
            "total_refs": self._n_static + self._n_derive + self._n_dynamic,
        }

    # ---------- 核心分析 ----------

    def analyze(self, changed):
        """计算 changed 配置项变更的影响面。

        推导路径：
          direct   = 直接读取 changed 的消费方，路径为 [changed]
          indirect = 沿 _dependents 反向边 BFS 得到的派生配置，
                     再取这些配置的静态消费方，路径记录完整派生链
          pending  = 模式命中 changed 名的动态引用（人工确认）
        """
        report = ImpactReport(changed=changed)
        start_fqn = changed.fqn()

        # 第一层：直接引用
        for consumer, src in self._readers.get(start_fqn, ()):
            report.direct.append(ImpactedItem(
                consumer=consumer,
                layer="direct",
                via=changed,
                path=(start_fqn,),
                ref_source=src,
            ))

        # 派生链 BFS：cur -> (fqn, path)
        queue = deque()
        for dep in self._dependents.get(start_fqn, ()):
            queue.append((dep, (start_fqn, dep)))
        seen = {start_fqn}
        while queue:
            fqn, path = queue.popleft()
            if fqn in seen:
                continue
            seen.add(fqn)
            ns, _, name = fqn.partition(":")
            via = ConfigKey(ns, name)
            for consumer, src in self._readers.get(fqn, ()):
                report.indirect.append(ImpactedItem(
                    consumer=consumer,
                    layer="indirect",
                    via=via,
                    path=path,
                    ref_source=src,
                ))
            for nxt in self._dependents.get(fqn, ()):
                if nxt not in seen:
                    queue.append((nxt, path + (nxt,)))

        # 动态拼接引用：同命名空间精确 glob 匹配 + 显式跨空间 "*"
        candidates = list(self._dynamic.get(changed.namespace, ()))
        if changed.namespace != self.WILDCARD_NS:
            candidates.extend(self._dynamic.get(self.WILDCARD_NS, ()))
        for consumer, pattern, expr, src in candidates:
            if fnmatchcase(changed.name, pattern):
                report.pending.append(PendingItem(
                    consumer=consumer,
                    namespace=changed.namespace,
                    pattern=pattern,
                    expression=expr,
                    matched=changed,
                    ref_source=src,
                ))
        return report
