"""关键帧检测器：在帧流上在线运行，只保留常数长度的滑动窗口。

判定依据（与 keyframe/metrics.py 对应，阈值见 config.py）：
- 硬切换 CUT：单帧直方图卡方 chi2 极大（内容整体替换），且空间
  变化比例 frac 也极高（并非“内容相同只是亮度跳变”），同时亮度
  差未达到闪光灯级别。
- 闪光灯 FLASH：chi2 大、|dmean| 也极大（整帧瞬时变亮/变暗）。
  不产生关键帧，并跳过该帧的后续判断。
- 渐变转场 GRADUAL：连续 GRAD_MIN_FRAMES 帧以上保持“中等 chi2 /
  中等 frac”（淡入淡出时 chi2 很低但 |dmean| 持续），累计变化
  达到 GRAD_MAX_DMEAN；渐变是内容 A 与 B 的像素级混合，因此
  每帧 frac 中等、方向一致。
- 快速运动 MOTION：单帧 frac 高但 chi2 低（直方图分布基本不变，
  只是像素位置移动）。不产生关键帧，只用于统计说明。
- 静态 STATIC：三项都接近 0。不产生关键帧。

状态机状态：IDLE / GRAD（正在跟踪疑似渐变）。输出事件：
cut / gradual / motion / static / flash（flash 仅调试用）。
"""

from collections import deque

from . import config
from .metrics import FrameFeatures, pair_metrics


class Event:
    __slots__ = ("kind", "index", "metrics", "duration")

    def __init__(self, kind, index, metrics, duration=1):
        self.kind = kind          # cut / gradual / motion / static / flash
        self.index = index        # 边界所在帧号（gradual 为结束帧）
        self.metrics = metrics    # (chi2, frac, dmean)，渐变时是结束对的值
        self.duration = duration  # gradual：持续帧数

    def __repr__(self):
        return f"Event({self.kind}@{self.index}, dur={self.duration})"


class KeyframeDetector:
    """流式检测器。喂入灰度帧 bytes，产出关键帧事件列表。

    内存占用与视频长度无关：只保留 3 帧原始帧（闪光灯判定需要
    i-2）、对应特征和两个待确认的窗口指标。
    """

    def __init__(self, width, height, cfg=None):
        self.width = width
        self.height = height
        self.cfg = cfg or config.Config()
        self.events = []
        self._frames = deque()      # 最近 3 帧 gray
        self._feats = deque()       # 最近 3 帧 FrameFeatures
        self._idx = -1
        # 渐变跟踪状态
        self._g_start = -1
        self._g_acc_dmean = 0.0
        self._g_max_chi2 = 0.0
        self._g_len = 0
        self._g_dir = 0
        # 运动/静态段统计
        self._seg_kind = None
        self._seg_start = 0
        self._flash_return = False   # 上一帧是闪光灯，本帧是恢复帧
        self._motion_segments = []   # (start, end)
        self._static_segments = []

    # ---- 对外接口 -------------------------------------------------
    def feed(self, gray):
        self._idx += 1
        i = self._idx
        feat = FrameFeatures(gray)
        self._frames.append(gray)
        self._feats.append(feat)
        if len(self._frames) > 3:
            self._frames.popleft()
            self._feats.popleft()
        if i == 0:
            return
        c, f, d = pair_metrics(
            self._feats[-2], self._feats[-1],
            self._frames[-2], self._frames[-1],
            self.width, self.height,
        )
        self._classify(i, c, f, d)

    def finish(self):
        """流结束：确认未决的渐变段，返回全部事件。"""
        if self._g_start >= 0:
            self._close_gradual(self._idx + 1, force=True)
        self._close_segment(self._idx + 1)
        return list(self.events)

    # ---- 内部状态机 -----------------------------------------------
    def _is_flash(self, i, c, f, d):
        if abs(d) < self.cfg.FLASH_DMEAN:
            return False
        # 需要确认 i-2->i-1 不是同样的强变化（闪光灯是孤立帧）
        if i >= 2 and len(self._feats) == 3:
            c0, f0, d0 = pair_metrics(
                self._feats[-3], self._feats[-2],
                self._frames[-3], self._frames[-2],
                self.width, self.height,
            )
            if c0 > self.cfg.MID_CHI2 and f0 > self.cfg.MID_FRAC:
                return False
        return c > self.cfg.MID_CHI2

    def _classify(self, i, c, f, d):
        cfg = self.cfg
        # 0) 闪光灯恢复帧：亮度跳回正常，不产出关键帧
        if self._flash_return:
            self._flash_return = False
            self._mark_segment("static", i)
            return

        # 1) 闪光灯：不产出关键帧，打断渐变跟踪
        if self._is_flash(i, c, f, d):
            self._abort_gradual()
            self._flash_return = True
            self._mark_segment("flash", i)
            self.events.append(Event("flash", i, (c, f, d)))
            return

        # 2) 硬切换
        if c > cfg.CUT_CHI2 and f > cfg.CUT_FRAC:
            self._abort_gradual()
            self.events.append(Event("cut", i, (c, f, d)))
            self._mark_segment("cut", i)
            return

        # 3) 渐变疑似帧：空间上没有整体替换（frac 低于切换），
        #    且存在持续变化迹象（chi2 缓升或亮度持续漂移）。
        #    注意不要求 frac 高于下限：淡入淡出/溶解在空间上很“安静”，
        #    也不要求 chi2 低于 CUT_CHI2：接近黑场时直方图变化可以很大，
        #    只要 frac 低就说明不是内容替换。
        fading = c > cfg.GRAD_CHI2_LOW or abs(d) > cfg.GRAD_DMEAN_STEP
        in_band = f < cfg.CUT_FRAC and fading
        if in_band:
            self._update_gradual(i, c, f, d)
            self._mark_segment("gradual", i)
            return

        # 4) 渐变段自然结束
        if self._g_start >= 0:
            self._close_gradual(i)
            if self.events and self.events[-1].kind == "gradual" \
                    and self.events[-1].index == i:
                self._mark_segment("gradual", i)
                return

        # 5) 快速运动：高空间变化但低直方图变化
        if f > cfg.MOTION_FRAC and c <= cfg.MOTION_CHI2:
            self._mark_segment("motion", i)
            self._note_segment_range("motion", i)
            return

        # 6) 静态
        self._mark_segment("static", i)

    def _update_gradual(self, i, c, f, d):
        cfg = self.cfg
        if self._g_start < 0:
            self._g_start = i
            self._g_acc_dmean = d
            self._g_max_chi2 = c
            self._g_len = 1
            self._g_dir = 1 if d >= 0 else -1
            return
        # 方向一致性：亮度漂移反转就结束本段（溶解中段 dmean 可接近 0，
        # 用 chi2/frac 仍在 band 内来维持，方向只约束累计量）
        if abs(d) > cfg.GRAD_DMEAN_STEP:
            if self._g_dir == 0:
                self._g_dir = 1 if d > 0 else -1
            self._g_acc_dmean += d
        self._g_max_chi2 = max(self._g_max_chi2, c)
        self._g_len += 1

    def _abort_gradual(self):
        self._g_start = -1
        self._g_len = 0
        self._g_acc_dmean = 0.0
        self._g_max_chi2 = 0.0
        self._g_dir = 0

    def _close_gradual(self, i, force=False):
        cfg = self.cfg
        if self._g_start < 0:
            return
        length = self._g_len
        acc = abs(self._g_acc_dmean)
        ok = (
            length >= cfg.GRAD_MIN_FRAMES
            and (
                acc >= cfg.GRAD_MAX_DMEAN
                or self._g_max_chi2 >= cfg.GRAD_CHI2_MIN
            )
        )
        if ok:
            self.events.append(Event(
                "gradual", i - (0 if force else 0),
                (self._g_max_chi2, 0.0,
                 self._g_dir * acc),
                duration=length,
            ))
        # 未被确认为渐变的中等变化段不产出关键帧
        self._abort_gradual()

    # ---- 运动/静态段范围记录（用于评估，不影响关键帧） ----------
    def _mark_segment(self, kind, i):
        if self._seg_kind != kind:
            self._close_segment(i)
            self._seg_kind = kind
            self._seg_start = i - 1

    def _close_segment(self, end):
        if self._seg_kind in ("motion", "static"):
            start = self._seg_start
            if end - start >= self.cfg.SEG_MIN_FRAMES:
                bucket = (self._motion_segments if self._seg_kind == "motion"
                          else self._static_segments)
                bucket.append((start, end))
        self._seg_kind = None

    def _note_segment_range(self, kind, i):
        # 段范围在 _close_segment 中统一记录，这里保留接口便于阅读
        pass
