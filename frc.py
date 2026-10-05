"""frc — 纯标准库视频帧率转换库（时间戳重采样）。

转换方式：最近邻重采样（Nearest-Neighbor Resampling），即"丢帧 / 复制"。
  - 升帧（目标帧率更高）：部分源帧被复制；
  - 降帧（目标帧率更低）：部分源帧被丢弃；
  - 不做像素插值（标准库无法解码像素，且插值会引入重影，见 README）。

防漂移核心：所有时间戳用 fractions.Fraction 精确有理数计算，
每一帧的源帧下标由"输出帧号 -> 源帧号"的独立映射直接算出，
不做任何逐步累加，因此累计时间偏移有严格上界且不随帧数增长。

映射公式（输出第 n 帧）：
  目标时刻   T(n) = t0 + n / dst_fps                 （精确，零漂移）
  源帧下标   k(n) = clamp(round_half_up(n * src_fps / dst_fps), 0, N-1)
  映射误差   |T(n) - (t0 + k(n)/src_fps)| <= 1 / (2 * src_fps)
             （最后一帧钳位时最多再放宽 1/(2*dst_fps)，见 README 推导）
"""

from fractions import Fraction

__all__ = ["FrameRateConverter", "to_fraction", "round_half_up"]


def to_fraction(value):
    """把 int / float / str("30000/1001") / (num, den) / Fraction 统一成 Fraction。"""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value, 1)
    if isinstance(value, float):
        # float 无法精确表示 29.97 等，限制分母取最接近的有理数
        return Fraction(value).limit_denominator(1_000_000)
    if isinstance(value, str):
        return Fraction(value)
    if isinstance(value, tuple) and len(value) == 2:
        return Fraction(int(value[0]), int(value[1]))
    raise TypeError(f"无法转换为分数: {value!r}")


def round_half_up(x):
    """对非负 Fraction 做四舍五入（逢半进一），结果确定、与平台无关。"""
    f = to_fraction(x)
    return (f.numerator * 2 + f.denominator) // (2 * f.denominator)


class FrameRateConverter:
    """把 num_src_frames 帧、起始时刻 t0、帧率 src_fps 的素材重采样到 dst_fps。"""

    def __init__(self, src_fps, dst_fps, num_src_frames, t0=0):
        self.src_fps = to_fraction(src_fps)
        self.dst_fps = to_fraction(dst_fps)
        if self.src_fps <= 0 or self.dst_fps <= 0:
            raise ValueError("帧率必须为正数")
        if num_src_frames < 0:
            raise ValueError("帧数不能为负")
        self.num_src = int(num_src_frames)
        self.t0 = to_fraction(t0)

        if self.num_src == 0:
            # 空素材 -> 空输出（确定行为）
            self.num_dst = 0
        else:
            # 输出帧数按时长等比换算并四舍五入，至少保留 1 帧（极短素材不消失）
            exact = Fraction(self.num_src) * self.dst_fps / self.src_fps
            self.num_dst = max(1, round_half_up(exact))

    # ---- 时间轴 ----

    def out_pts(self, out_index):
        """输出第 n 帧的显示时刻（目标时间轴上的精确位置）。"""
        return self.t0 + Fraction(out_index) / self.dst_fps

    def src_pts(self, src_index):
        """源第 k 帧的原始显示时刻。"""
        return self.t0 + Fraction(src_index) / self.src_fps

    @property
    def src_duration(self):
        return Fraction(self.num_src) / self.src_fps

    @property
    def dst_duration(self):
        return Fraction(self.num_dst) / self.dst_fps

    # ---- 帧映射 ----

    def src_index_for(self, out_index):
        """输出第 n 帧应取哪一帧源画面（最近邻，独立计算，不累加）。"""
        if self.num_src == 0:
            raise IndexError("空素材没有帧")
        ideal = Fraction(out_index) * self.src_fps / self.dst_fps
        k = round_half_up(ideal)
        return min(max(k, 0), self.num_src - 1)

    def mapping_error(self, out_index):
        """输出帧目标时刻与被选中源帧原始时刻的偏差（秒，Fraction，可正可负）。"""
        return self.out_pts(out_index) - self.src_pts(self.src_index_for(out_index))

    def iter_map(self):
        """流式产出 (输出帧号, 输出时刻, 源帧号)，内存 O(1)。"""
        for n in range(self.num_dst):
            yield n, self.out_pts(n), self.src_index_for(n)

    def convert_frames(self, frames):
        """对真实帧序列（列表/迭代器，元素为不透明帧对象）执行丢帧/复制。"""
        frames = list(frames)
        if len(frames) != self.num_src:
            raise ValueError(f"帧数不符: 期望 {self.num_src}, 实际 {len(frames)}")
        return [frames[self.src_index_for(n)] for n in range(self.num_dst)]

    # ---- 校验 ----

    def timeline_drift(self, out_index):
        """输出帧时刻与目标时间轴的偏差。本实现按构造恒为 0（对拍用）。"""
        ideal = self.t0 + Fraction(out_index) / self.dst_fps
        return self.out_pts(out_index) - ideal

    def max_abs_mapping_error(self):
        """扫描全部输出帧，返回实际最大 |映射误差|（秒，Fraction）。"""
        worst = Fraction(0)
        for n in range(self.num_dst):
            e = abs(self.mapping_error(n))
            if e > worst:
                worst = e
        return worst

    def theoretical_bound(self):
        """映射误差理论上界：半帧源间隔；尾帧钳位时再加半帧目标间隔。"""
        return Fraction(1, 2) / self.src_fps + Fraction(1, 2) / self.dst_fps
