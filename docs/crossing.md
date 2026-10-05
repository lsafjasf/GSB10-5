# 音视频轨迹起止交叉数据

交叉区间在公共微秒时间基下计算（`avmux/report.py` 的 `analyze()`）：

```
overlap_start = max(各轨 start_us)      # start: ROUND_NEAREST
overlap_end   = min(各轨 end_us)        # end:   ROUND_DOWN（不越界）
```

end 用向下取整，保证交集 `[start, end)` 一定严格落在所有轨迹内部；
精确值（Fraction）与取整值同时保留。时长不一致、不重叠都只**报告**，
绝不自动补齐。

以下数据由 `python3 demo_crossing.py` 产生，原始输出见
`docs/crossing_output.txt`。

```

=== 双轨迹对齐 1.0s（tb 1/1000 vs 1/90000） ===
  audio tb=1/1000s  ticks [0, 1000)  us [0, 1000000)  exact_end_us=1000000.000000 (1000000)
  video tb=1/90000s  ticks [0, 90000)  us [0, 1000000)  exact_end_us=1000000.000000 (1000000)
  交叉区间(公共us基): [0, 1000000) 时长 1000000 us
  精确边界: start=0.000000 (0)  end=1000000.000000 (1000000)

=== 48k 音频 47 帧 vs 90k 视频 30 帧@29.97fps（时长不一致） ===
  audio tb=1/48000s  ticks [0, 48128)  us [0, 1002666)  exact_end_us=1002666.666667 (3008000/3)
  video tb=1/90000s  ticks [0, 90090)  us [0, 1001000)  exact_end_us=1001000.000000 (1001000)
  [warning] duration_mismatch: track duration mismatch: audio=1002666us vs video=1001000us (diff=-1666us); not auto-padded
  交叉区间(公共us基): [0, 1001000) 时长 1001000 us
  精确边界: start=0.000000 (0)  end=1001000.000000 (1001000)

=== 音频晚 5ms 开始（起点交叉） ===
  audio tb=1/1000s  ticks [5, 105)  us [5000, 105000)  exact_end_us=105000.000000 (105000)
  video tb=1/90000s  ticks [0, 18000)  us [0, 200000)  exact_end_us=200000.000000 (200000)
  [warning] duration_mismatch: track duration mismatch: audio=100000us vs video=200000us (diff=+100000us); not auto-padded
  交叉区间(公共us基): [5000, 105000) 时长 100000 us
  精确边界: start=5000.000000 (5000)  end=105000.000000 (105000)

=== 音视频完全不重叠 ===
  audio tb=1/1000s  ticks [0, 50)  us [0, 50000)  exact_end_us=50000.000000 (50000)
  video tb=1/1000s  ticks [1000, 1050)  us [1000000, 1050000)  exact_end_us=1050000.000000 (1050000)
  [warning] no_overlap: tracks do not overlap in time: [1000000, 50000)us
  交叉区间: 无
```

## 结果解读

1. **双轨迹对齐 1.0 s**（音频 `1/1000`、视频 `1/90000`）：
   两条轨迹在公共 µs 基下都是 `[0, 1000000)`，交叉 `[0, 1000000)`，
   时长 1000000 µs，无报告项。时间基不同，但经过换算后完全对齐。
2. **真实 48 kHz 音频 vs 90 kHz 29.97 fps 视频**：
   音频 `[0, 1002666)` µs（精确末尾 1002666.666…），视频
   `[0, 1001000)` µs，时长差 1666 µs → `warning/duration_mismatch`，
   交叉区间取较短侧 `[0, 1001000)`。没有任何一帧被补齐或截断。
3. **音频晚 5 ms 开始**：交叉从 5000 µs 开始（起点被音频的晚开始收窄）。
4. **完全不重叠**：`overlap_start >= overlap_end` → `warning/no_overlap`，
   `has_overlap=False`。
