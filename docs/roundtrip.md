# 解封装再封装对拍数据

`python3 demo_roundtrip.py` 完整演示：原始媒体 → 封装 → 解封装 → 再封装，
并逐样本比较 `(pts, dts, duration, keyframe, payload)`，再对两次封装做
**字节级**比较。原始输出见 `docs/roundtrip_output.txt`：

```
== 原始媒体 ==
  [original]
    audio tb=1/48000s n=47 first(pts=0,dts=0,dur=1024,kf=False,data=8813820cc1cc6601..) last(pts=47104,dts=47104,dur=1024) sha256(all)=77a830caa0aa60b5
    video tb=1/90000s n=30 first(pts=0,dts=0,dur=3003,kf=True,data=4602f8c7a3d254d7..) last(pts=87087,dts=87087,dur=3003) sha256(all)=1a2831e15af966f6

== 封装 #1 ==  文件大小 5663 字节
  [warning] duration_mismatch: track duration mismatch: audio=1002666us vs video=1001000us (diff=-1666us); not auto-padded

== 解封装 ==
  [demuxed]
    audio tb=1/48000s n=47 first(pts=0,dts=0,dur=1024,kf=False,data=8813820cc1cc6601..) last(pts=47104,dts=47104,dur=1024) sha256(all)=77a830caa0aa60b5
    video tb=1/90000s n=30 first(pts=0,dts=0,dur=3003,kf=True,data=4602f8c7a3d254d7..) last(pts=87087,dts=87087,dur=3003) sha256(all)=1a2831e15af966f6

== 再封装 #2 ==  文件大小 5663 字节

两次封装字节级一致: True
逐样本对拍（pts/dts/duration/keyframe/payload）: 全部一致
```

## 结论

- 两条轨迹、77 个样本逐字段对拍**全部一致**，包括每帧 payload 与 keyframe 标记。
- 两次封装产物**字节级完全相同**（时间戳以各自时间基原样落盘，无隐式换算）。
- 时长不一致（1666 µs）在封装报告中以 warning 呈现，但数据本身原样保留。
- 1 小时素材的对拍结果见 `docs/benchmark.md`：276750 个样本不一致数为 0。

对拍代码：`selftest.py` 的 `compare_media()`（unittest 断言）、
`benchmark.py`（逐字段 + 字节级）。
