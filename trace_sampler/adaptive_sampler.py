"""自适应追踪采样率控制器（仅使用 Python 标准库）。

核心思想：头部采样（head-based sampling）。
  - 采样决策只取决于 trace_id 的哈希值与当前采样率，
    同一条链路（trace）上的所有节点/跨度（span）用同一个 trace_id 做决策，
    因此整条链路要么整体被采样，要么整体被丢弃，不会出现部分节点被采样。
  - 含错误的链路（has_error=True）无条件保留，优先于采样率与流量预算。

采样率自适应调整依据（每个控制窗口调用一次 update）：
  1. 当前量 qps：本窗口实际观测到的链路吞吐（条/秒）。
  2. 上限 max_qps：下游可承受的采样后吞吐预算。
  3. 错误率 err_rate：本窗口错误链路占比。

调整公式：
  smooth_qps   = smooth * qps + (1 - smooth) * smooth_qps      （指数平滑）
  effective_qps = max(smooth_qps, qps)                          （突增取瞬时值，快速响应）
  target = max_qps / effective_qps                              （基础目标：量超上限则等比压缩）
  若 err_rate > err_threshold:
      target *= 1 + err_boost * (err_rate - err_threshold)      （错误率骤升时提升整体采样率，
                                                                 保留错误现场上下文）
  target 截断到 [min_rate, 1.0]
  若 target < 当前 rate：立即下调（突增一拍内跟上）
  否则：rate = min(target, rate * max_growth)                   （恢复限速，避免抖动）
"""

import hashlib

__all__ = ["AdaptiveSampler"]

_UINT64 = float(1 << 64)


def _hash_to_unit(trace_id):
    """把 trace_id 确定性映射到 [0, 1)。同一 trace_id 永远得到同一个值。"""
    if isinstance(trace_id, str):
        trace_id = trace_id.encode("utf-8")
    digest = hashlib.md5(trace_id).digest()
    return int.from_bytes(digest[:8], "big") / _UINT64


class AdaptiveSampler:
    """自适应头部采样器。决策无状态（仅依赖 trace_id 与当前 rate），内存 O(1)。"""

    def __init__(
        self,
        max_qps,
        min_rate=1e-4,
        err_threshold=0.05,
        err_boost=4.0,
        smooth=0.5,
        max_growth=2.0,
    ):
        if max_qps <= 0:
            raise ValueError("max_qps 必须为正数")
        if not 0 < min_rate <= 1.0:
            raise ValueError("min_rate 必须在 (0, 1] 区间")
        self.max_qps = float(max_qps)        # 采样后吞吐上限（条/秒）
        self.min_rate = float(min_rate)      # 采样率下限，保证低流量时仍有数据
        self.err_threshold = float(err_threshold)  # 错误率阈值，超过则提升采样率
        self.err_boost = float(err_boost)    # 错误率超阈值后的提升系数
        self.smooth = float(smooth)          # 吞吐平滑系数
        self.max_growth = float(max_growth)  # 每窗口采样率最大恢复倍数
        self._rate = 1.0
        self._smooth_qps = None

    @property
    def rate(self):
        """当前采样率。"""
        return self._rate

    def should_sample(self, trace_id, has_error=False):
        """对一条链路做采样决策。

        - has_error=True 的链路无条件保留（异常链路优先）。
        - 其余链路：hash(trace_id) < rate 则保留。决策只依赖 trace_id，
          同一 trace_id 在同一控制窗口内必然得到相同结果（整链一致）。
        """
        if has_error:
            return True
        return _hash_to_unit(trace_id) < self._rate

    def update(self, qps, err_rate=0.0):
        """窗口结束时上报观测值并调整采样率，返回新采样率。"""
        if qps < 0:
            raise ValueError("qps 不能为负")
        if self._smooth_qps is None:
            self._smooth_qps = float(qps)
        else:
            self._smooth_qps = self.smooth * qps + (1.0 - self.smooth) * self._smooth_qps

        effective_qps = max(self._smooth_qps, float(qps))
        if effective_qps <= 0.0:
            target = 1.0
        else:
            target = self.max_qps / effective_qps

        if err_rate > self.err_threshold:
            target *= 1.0 + self.err_boost * (err_rate - self.err_threshold)

        target = min(1.0, max(self.min_rate, target))

        if target < self._rate:
            self._rate = target  # 流量突增：立即下调，一个窗口内跟上
        else:
            self._rate = min(target, self._rate * self.max_growth)  # 突降后限速恢复
        return self._rate
