"""动态日志级别管理库（仅标准库）。

核心能力：
1. 级别调整按"版本号 + 推送"传播到各节点，记录每节点生效时刻；
2. 令牌桶限流，超限自动收敛（逐级抬高生效级别），恢复后自动回降；
3. 调整全部留痕：谁、何时、把哪个源、从什么级别改成什么级别；
4. 一致性校验：逐节点比对已应用版本与控制器当前版本。

时钟使用可注入的虚拟时钟（self.now，单位秒），便于确定性仿真与自测；
真实部署时 advance() 由实际时钟驱动，传播延迟对应控制通道 RTT。
"""

import heapq
import itertools

LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
ORDERED_LEVELS = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
_LEVEL_INDEX = {name: i for i, name in enumerate(ORDERED_LEVELS)}


class AuditRecord:
    """一条可追溯的调整记录（__slots__ 压缩 10 万级记录的内存）。"""

    __slots__ = ("seq", "version", "source", "old_level", "new_level",
                 "actor", "reason", "timestamp")

    def __init__(self, seq, version, source, old_level, new_level,
                 actor, reason, timestamp):
        self.seq = seq
        self.version = version
        self.source = source
        self.old_level = old_level
        self.new_level = new_level
        self.actor = actor
        self.reason = reason
        self.timestamp = timestamp

    def as_dict(self):
        return {s: getattr(self, s) for s in self.__slots__}

    def __repr__(self):
        return ("AuditRecord(seq=%d v=%d t=%.3f %s %s:%s->%s by=%s %s)"
                % (self.seq, self.version, self.timestamp, self.source,
                   self.old_level, "?" if self.old_level is None else "",
                   self.new_level, self.actor, self.reason)).replace("->?", "->")


class _Source:
    __slots__ = ("desired", "override", "version",
                 "tokens", "last_refill",
                 "win_passed", "win_dropped", "win_filtered",
                 "win_start", "cooldown")

    def __init__(self, level):
        self.desired = level            # 用户设定级别
        self.override = None            # 自动收敛叠加级别（只高不低）
        self.version = -1               # 尚未对任何节点生效
        self.tokens = 0.0
        self.last_refill = 0.0
        self.win_passed = 0
        self.win_dropped = 0
        self.win_filtered = 0
        self.win_start = 0.0
        self.cooldown = 0


class _Node:
    __slots__ = ("node_id", "latency", "applied")

    def __init__(self, node_id, latency):
        self.node_id = node_id
        self.latency = latency          # 控制通道推送延迟（秒）
        # source -> (version, level)，已应用的最新版本
        self.applied = {}


class Manager:
    """动态日志级别控制器。

    rate_limit      每源每秒允许通过的日志条数上限。
    burst           令牌桶容量，默认等于 rate_limit（允许 1 秒突发）。
    drop_threshold  窗口内丢弃占比达到该值即触发一级自动收敛。
    cooldown_windows 无丢弃后连续多少个统计窗口才回降一级。
    """

    SYSTEM_ACTOR = "system:auto-converge"
    RESTORE_ACTOR = "system:auto-restore"

    def __init__(self, rate_limit=1000.0, burst=None, drop_threshold=0.05,
                 cooldown_windows=3, auto_converge=True):
        self.rate_limit = float(rate_limit)
        self.burst = float(burst) if burst is not None else float(rate_limit)
        self.drop_threshold = drop_threshold
        self.cooldown_windows = cooldown_windows
        self.auto_converge = auto_converge

        self.now = 0.0
        self._sources = {}
        self._nodes = []
        self._audit = []
        self._version = itertools.count()
        self._seq = itertools.count()
        # (deliver_time, heap_seq, node_index, version, source, level)
        self._inflight = []
        self._heapseq = itertools.count()
        # version -> {"source":..., "level":..., "publish":...,
        #             "effective": {node_id: time}}
        self._propagation = {}

    # ---------- 拓扑管理 ----------

    def add_source(self, name, level="INFO"):
        if name in self._sources:
            raise ValueError("source already exists: %s" % name)
        src = _Source(level)
        src.tokens = self.burst
        src.last_refill = self.now
        src.win_start = self.now
        self._sources[name] = src
        for node in self._nodes:  # 既有节点预置初始级别（版本 -1）
            node.applied[name] = (-1, level)
        return src

    def add_node(self, node_id, latency):
        node = _Node(node_id, latency)
        self._nodes.append(node)
        for name, src in self._sources.items():  # 既有源预置初始级别
            node.applied[name] = (-1, src.desired)
        return node_id

    @property
    def audit(self):
        return self._audit

    def effective_level(self, source):
        """控制器侧当前生效级别 = max(用户设定, 自动收敛叠加)。"""
        src = self._sources[source]
        if src.override is None:
            return src.desired
        return (src.desired if _LEVEL_INDEX[src.desired] >=
                _LEVEL_INDEX[src.override] else src.override)

    def node_level(self, node_id, source):
        """某节点上当前实际生效的级别；未送达前为 None（沿用旧级别）。"""
        node = next(n for n in self._nodes if n.node_id == node_id)
        rec = node.applied.get(source)
        return None if rec is None else rec[1]

    # ---------- 调整与传播 ----------

    def adjust(self, source, level, actor, reason=""):
        """人工/调用方调整级别，返回新版本号。

        传播方式：控制器为本次调整分配单调递增 version，按各节点控制通道
        延迟推送；latency==0（本地/同进程节点）立即生效，其余在 advance()
        推进到 deliver_time 后生效，并记录各节点生效时刻。
        """
        if level not in LEVELS:
            raise ValueError("bad level: %s" % level)
        src = self._sources[source]
        old = src.desired
        if old == level:
            return src.version
        version = next(self._version)
        src.desired = level
        src.version = version
        record = AuditRecord(next(self._seq), version, source, old, level,
                             actor, reason, self.now)
        self._audit.append(record)
        self._propagation[version] = {
            "source": source, "level": level, "publish": self.now,
            "effective": {}}
        eff = self.effective_level(source)
        for idx, node in enumerate(self._nodes):
            if node.latency <= 0.0:
                self._apply(node, version, source, eff)
            else:
                heapq.heappush(self._inflight,
                               (self.now + node.latency,
                                next(self._heapseq), idx,
                                version, source, eff))
        return version

    def _apply(self, node, version, source, level, ts=None):
        # 旧版本乱序到达不允许覆盖新版本（版本号守门）
        prev = node.applied.get(source)
        if prev is not None and prev[0] >= version:
            return
        node.applied[source] = (version, level)
        self._propagation[version]["effective"][node.node_id] = (
            self.now if ts is None else ts)

    def advance(self, dt):
        """推进虚拟时钟：送达到期变更，并按 1 秒窗口评估自动收敛。"""
        self.now += dt
        self._deliver()
        self._rotate_windows()

    def _deliver(self):
        while self._inflight and self._inflight[0][0] <= self.now:
            deliver_at, _, idx, version, source, level = heapq.heappop(
                self._inflight)
            self._apply(self._nodes[idx], version, source, level,
                        deliver_at)

    def _rotate_windows(self):
        for name, src in self._sources.items():
            if self.now < src.win_start + 1.0:
                continue
            self._converge_window(name, src)
            src.win_passed = 0
            src.win_dropped = 0
            src.win_filtered = 0
            src.win_start = self.now

    def _converge_window(self, name, src):
        if not self.auto_converge:
            return
        total = src.win_passed + src.win_dropped + src.win_filtered
        if total == 0:
            if src.override is not None:
                # 无流量窗口同样安全，计入冷却，逐级回降
                src.cooldown += 1
                if src.cooldown >= self.cooldown_windows:
                    self._lower_override(name, src)
            return
        drop_ratio = src.win_dropped / total
        if src.win_dropped > 0 and drop_ratio >= self.drop_threshold:
            self._raise_override(name, src, drop_ratio)
            return
        if src.win_dropped == 0 and src.override is not None:
            src.cooldown += 1
            if src.cooldown >= self.cooldown_windows:
                self._lower_override(name, src)

    def _publish_override(self, name, src, new_override, actor, reason):
        old_eff = self.effective_level(name)
        src.override = new_override
        new_eff = self.effective_level(name)
        if new_eff == old_eff:
            return
        version = next(self._version)
        src.version = version
        self._audit.append(AuditRecord(
            next(self._seq), version, name, old_eff, new_eff,
            actor, reason, self.now))
        self._propagation[version] = {
            "source": name, "level": new_eff, "publish": self.now,
            "effective": {}}
        for idx, node in enumerate(self._nodes):
            if node.latency <= 0.0:
                self._apply(node, version, name, new_eff)
            else:
                heapq.heappush(self._inflight,
                               (self.now + node.latency,
                                next(self._heapseq), idx,
                                version, name, new_eff))

    def _raise_override(self, name, src, drop_ratio):
        src.cooldown = 0
        base = self.effective_level(name)
        i = _LEVEL_INDEX[base]
        if i >= len(ORDERED_LEVELS) - 1:
            return
        new_override = ORDERED_LEVELS[i + 1]
        self._publish_override(
            name, src, new_override, self.SYSTEM_ACTOR,
            "drop=%.1f%% in window %.1f-%.1f"
            % (drop_ratio * 100.0, src.win_start, self.now))

    def _lower_override(self, name, src):
        src.cooldown = 0
        i = _LEVEL_INDEX[src.override]
        new_override = ORDERED_LEVELS[i - 1]
        if _LEVEL_INDEX[new_override] <= _LEVEL_INDEX[src.desired]:
            new_override = None  # 叠加级别回到用户设定之下即撤销
        self._publish_override(
            name, src, new_override, self.RESTORE_ACTOR,
            "no drops for %d windows" % self.cooldown_windows)

    # ---------- 日志写入与限流 ----------

    def emit(self, source, level, count=1):
        """记录 count 条 level 级日志，返回 (passed, dropped, filtered)。

        filtered：低于生效级别被级别过滤的条数（不进限流）；
        dropped  ：级别通过但超出速率上限被丢弃的条数。
        """
        src = self._sources[source]
        if self.now >= src.win_start + 1.0:
            self._converge_window(source, src)
            src.win_passed = 0
            src.win_dropped = 0
            src.win_filtered = 0
            src.win_start = self.now

        eff = self.effective_level(source)
        if LEVELS[level] < LEVELS[eff]:
            src.win_filtered += count
            return 0, 0, count

        elapsed = self.now - src.last_refill
        src.tokens = min(self.burst, src.tokens + elapsed * self.rate_limit)
        src.last_refill = self.now
        passed = int(min(count, src.tokens))
        dropped = count - passed
        src.tokens -= passed
        src.win_passed += passed
        src.win_dropped += dropped
        return passed, dropped, 0

    # ---------- 一致性与传播观测 ----------

    def check_consistency(self):
        """逐节点比对已应用版本与控制器当前版本。

        返回 {"ok": bool, "nodes": N, "sources": M, "mismatches": [...]}。
        调回操作完成且在途变更全部送达后，mismatches 必须为空。
        """
        self._deliver()
        mismatches = []
        for node in self._nodes:
            for name, src in self._sources.items():
                rec = node.applied.get(name)
                node_version = -1 if rec is None else rec[0]
                node_level = None if rec is None else rec[1]
                want_level = self.effective_level(name)
                if node_version != src.version or node_level != want_level:
                    mismatches.append({
                        "node": node.node_id, "source": name,
                        "node_version": node_version,
                        "want_version": src.version,
                        "node_level": node_level, "want_level": want_level})
        return {"ok": not mismatches, "nodes": len(self._nodes),
                "sources": len(self._sources),
                "mismatches": mismatches}

    def propagation_report(self, version):
        """返回某版本各节点生效时刻与相对发布时刻的延迟（秒）。"""
        info = self._propagation[version]
        rows = []
        for node in self._nodes:
            t = info["effective"].get(node.node_id)
            rows.append({"node": node.node_id, "channel_latency": node.latency,
                         "effective_time": None if t is None else t,
                         "effective_delay": None if t is None
                                            else t - info["publish"]})
        return {"version": version, "source": info["source"],
                "level": info["level"], "publish_time": info["publish"],
                "rows": rows}

    def all_inflight_delivered(self):
        return not self._inflight
