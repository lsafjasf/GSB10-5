"""引用关系图构建。

引用关系的三种建立方式与覆盖边界:

1. 静态引用 (add_static / add_dependency)
   代码、部署清单、依赖声明中以字面量出现的完全限定配置名 (scope.name)。
   覆盖边界: 只覆盖扫描到的字面量; 运行期改名、反射、外部系统直连不覆盖。

2. 间接引用 (add_config 的模板值 / add_dependency 的依赖链)
   - 配置值中的 ${other.scope.key} 模板占位符, 形成 配置->配置 边;
   - 服务->模块->配置 的依赖链, 通过反向可达性传播影响。
   覆盖边界: 模板语法限定为 ${...}; 自定义模板引擎需自行展开后再录入。

3. 动态拼接引用 (add_dynamic)
   形如 cache.{region}.ttl 的模式:
   - 变量域已知 (domain={"region": [...]}): 展开后与已登记配置求交, 命中才建边;
   - 变量域未知: 记入 unresolved 清单, 分析时按结构匹配列入"待人工确认",
     绝不静默丢弃。
   覆盖边界: 变量域由调用方声明; 声明不全等同于未知, 会降级为待人工确认。

同名不同义的判定: 配置项身份 = 完全限定名 (scope.name)。引用必须携带 scope,
仅当完全限定名逐段相等时才建立引用边, 因此 payment.db.timeout 与
search.db.timeout 是两个不同节点, 不会互相误判。
"""

import re

_TEMPLATE_RE = re.compile(r"\$\{([^}]+)\}")
_VAR_RE = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")


def expand_pattern(pattern, domain):
    """按变量域展开动态模式, 返回候选完全限定名列表。"""
    parts = [pattern]
    for var, values in domain.items():
        token = "{" + var + "}"
        nxt = []
        for p in parts:
            if token in p:
                nxt.extend(p.replace(token, v) for v in values)
            else:
                nxt.append(p)
        parts = nxt
    return parts


def pattern_struct_match(pattern, key):
    """结构匹配: 模式与 key 段数相同, 且字面量段逐段相等 ({var} 段通配)。"""
    p = pattern.split(".")
    k = key.split(".")
    if len(p) != len(k):
        return False
    for pp, kk in zip(p, k):
        if pp == kk:
            continue
        if _VAR_RE.fullmatch(pp):
            continue
        return False
    return True


class ImpactGraph:
    """引用关系图。节点为配置项/服务/模块, 边表示 "依赖于"。"""

    def __init__(self):
        self._ids = {}
        self.names = []
        self._rev = []               # 反向邻接表: 被依赖方 -> [依赖方...]
        self.config_keys = set()     # 已登记配置的完全限定名
        self._pending_dynamic = []   # (src, [展开后的候选名])
        self.unresolved = []         # (src, pattern) 无法静态判定的动态引用
        self._finalized = False

    def _nid(self, name):
        nid = self._ids.get(name)
        if nid is None:
            nid = len(self.names)
            self._ids[name] = nid
            self.names.append(name)
            self._rev.append([])
        return nid

    def _edge(self, src, dst):
        self._rev[self._nid(dst)].append(self._nid(src))

    def add_config(self, fqn, value=None):
        """登记配置项; value 中的 ${...} 形成配置->配置间接引用边。"""
        self._nid(fqn)
        self.config_keys.add(fqn)
        if value is not None:
            for m in _TEMPLATE_RE.finditer(str(value)):
                self._edge(fqn, m.group(1).strip())

    def add_static(self, source, key):
        """静态引用: source(服务/模块) 以字面量引用配置 key。"""
        self._edge(source, key)

    def add_dependency(self, source, target):
        """依赖链: source 依赖 target(模块/服务), 用于间接影响传播。"""
        self._edge(source, target)

    def add_dynamic(self, source, pattern, domain=None):
        """动态拼接引用。domain 已知则延迟展开建边, 未知则进待确认清单。"""
        if not domain:
            self.unresolved.append((source, pattern))
            return
        expanded = expand_pattern(pattern, domain)
        if any(_VAR_RE.search(p) for p in expanded):
            self.unresolved.append((source, pattern))
            return
        self._pending_dynamic.append((source, expanded))

    def finalize(self):
        """解析延迟的动态引用: 展开候选与已登记配置求交, 命中才建边。"""
        for src, patterns in self._pending_dynamic:
            for p in patterns:
                if p in self.config_keys:
                    self._edge(src, p)
        self._pending_dynamic = []
        self._finalized = True
        return self

    @property
    def edge_count(self):
        return sum(len(v) for v in self._rev)
