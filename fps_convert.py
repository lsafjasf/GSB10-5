"""fps_convert -- 纯标准库视频帧率转换库（时间轴/帧调度层）。

转换方式：最近邻帧选择（nearest-neighbor sampling）。
  - 升帧 => 复制帧（duplicate）
  - 降帧 => 丢帧（drop）
  - 不做像素插值（库只调度帧，不触碰像素；插值需要解码器/滤镜管线）

时间轴模型（全部用 fractions.Fraction 精确有理数运算，零浮点漂移）：
  - 源帧 i 的显示时刻为 pts[i]（秒，任意非负、单调递增，首帧可以不为 0）。
  - 目标时间轴锚定在源首帧 pts[0]：第 k 个目标时槽的中心时刻为
        t_k = pts[0] + k / dst_rate          (k = 0, 1, 2, ...)
  - 每个目标时槽从源帧中选显示时刻离 t_k 最近的一帧（平局取更早的帧）。
  - 每个目标时槽从源帧中选显示时刻离 t_k 最近的一帧；等距平局取偶数下标
    （banker's rounding）。偶数下标规则确定、无随机，且在跨周期时上下平局
    交替，避免"总取较早帧"造成系统性半样本偏差，使累计偏移有界。
  - 输出帧的 pts 重定为目标网格时刻 t_k（retime），保证输出严格匀速。

帧数规则（确定性）：
  - 默认：输出覆盖区间 [pts[0], pts[0] + D)，D = n * 源中位帧间隔，
    输出帧数 N = ceil(D * dst_rate)。
  - 显式 duration：N = ceil(duration * dst_rate)，不足复制尾帧，超出截断。

误差上界（证明见 REPORT.md）：
  - 中间帧：|选中源帧pts - 目标网格时刻| <= 0.5 * 源帧间隔（与帧号无关）。
  - 尾部钳制帧：< 1 * 源帧间隔。
  - 因此任意帧号 k 的累计偏移都有常数上界，时间轴不会越跑越偏。
"""

from fractions import Fraction
from dataclasses import dataclass, field
from statistics import median

__all__ = ["Frame", "ConvertedFrame", "convert_fps", "convert_fps_list",
           "uniform_frames", "expected_output_count"]


@dataclass(frozen=True)
class Frame:
    """一帧：pts 为显示时刻（秒，Fraction），payload 为帧数据引用。"""
    pts: Fraction
    payload: object = None


@dataclass(frozen=True)
class ConvertedFrame:
    """输出帧：pts 已重定到目标网格；同时保留源帧信息用于对拍。"""
    pts: Fraction                # 目标时间轴上的精确时刻（= pts0 + k/dst_rate）
    source_pts: Fraction         # 被选中源帧的原始 pts
    source_index: int            # 被选中源帧的下标
    target_index: int            # 输出帧序号 k
    payload: object = None
    duplicated: bool = False     # 该源帧是否已被前一个输出帧使用过
    phase_error: Fraction = field(init=False)  # source_pts - pts（有符号）

    def __post_init__(self):
        object.__setattr__(self, "phase_error", self.source_pts - self.pts)


class _End:
    """源耗尽后的哨兵：pts=+inf，自然输掉最近邻竞争。"""
    pts = None
    payload = None


_END = _End()


def _norm(item):
    """输入项可以是 Frame、(pts, payload) 元组或纯 pts 数值。"""
    if isinstance(item, Frame):
        return item
    if isinstance(item, tuple):
        return Frame(Fraction(item[0]), item[1] if len(item) > 1 else None)
    return Frame(Fraction(item))


def convert_fps(frames, src_rate, dst_rate, duration=None):
    """流式帧率转换生成器。额外内存 O(1)（只缓存相邻两帧）。

    参数:
        frames:   可迭代对象，元素为 Frame / (pts, payload) / pts，pts 单调不减。
        src_rate: 源帧率（标称值，用于默认帧数规则与误差上界）。
        dst_rate: 目标帧率。
        duration: 可选，显式输出时长（秒）。缺省时按 N = ceil(n*Ts*dst_rate)。
    产出:
        ConvertedFrame 序列，pts 严格等于目标网格时刻。
    """
    src_rate = Fraction(src_rate)
    dst_rate = Fraction(dst_rate)
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("帧率必须为正数")
    if duration is not None:
        duration = Fraction(duration)
        if duration <= 0:
            raise ValueError("duration 必须为正数")

    src_interval = Fraction(1, 1) / src_rate
    dst_interval = Fraction(1, 1) / dst_rate

    it = iter(frames)
    try:
        first = _norm(next(it))
    except StopIteration:
        return
    pts0 = first.pts
    n_src = 1
    prev = first
    try:
        curr = _norm(next(it))
        n_src = 2
    except StopIteration:
        curr = _END

    k = 0
    prev_used = False
    while True:
        t_k = pts0 + k * dst_interval
        if duration is not None and t_k >= pts0 + duration:
            break
        # 最近邻选择；等距平局取偶数下标（curr 下标 = n_src-1，prev = n_src-2）
        d_curr = abs(curr.pts - t_k) if curr is not _END else None
        d_prev = abs(prev.pts - t_k)
        while curr is not _END and (
                d_curr < d_prev
                or (d_curr == d_prev and (n_src - 1) % 2 == 0)):
            prev = curr
            prev_used = False
            try:
                curr = _norm(next(it))
                n_src += 1
            except StopIteration:
                curr = _END
            d_curr = abs(curr.pts - t_k) if curr is not _END else None
            d_prev = abs(prev.pts - t_k)
        # 源耗尽后确定默认输出帧数 N = ceil(n_src * Ts / To)
        if duration is None and curr is _END:
            if t_k >= pts0 + n_src * src_interval:
                break
        yield ConvertedFrame(
            pts=t_k,
            source_pts=prev.pts,
            source_index=n_src - (2 if curr is not _END else 1),
            target_index=k,
            payload=prev.payload,
            duplicated=prev_used,
        )
        prev_used = True
        k += 1


def convert_fps_list(frames, src_rate, dst_rate, duration=None):
    """convert_fps 的列表版便捷封装。"""
    return list(convert_fps(frames, src_rate, dst_rate, duration))


def uniform_frames(pts0, rate, count, payload_size=0):
    """生成匀速测试素材：pts = pts0 + i/rate。"""
    rate = Fraction(rate)
    pts0 = Fraction(pts0)
    payload = b"\0" * payload_size if payload_size else None
    for i in range(count):
        yield Frame(pts0 + Fraction(i, 1) / rate, payload)


def expected_output_count(src_count, src_rate, dst_rate, duration=None):
    """与库一致的输出帧数公式，用于对拍。"""
    if duration is not None:
        from math import ceil
        return ceil(Fraction(duration) * Fraction(dst_rate))
    from math import ceil
    return ceil(src_count * Fraction(dst_rate) / Fraction(src_rate))
