# 1 小时素材封装/解封装耗时与内存

素材（`benchmark.py` 生成，确定性）：

- 音频：48 kHz，时间基 `1/48000`，1024 tick/帧 × 168750 帧 = 3600.0 s，32 B/帧
- 视频：时间基 `1/90000`，3000 tick/帧（30 fps）× 108000 帧 = 3600.0 s，64 B/帧
- 合计 276750 个样本，payload 约 12.3 MB，封装文件 19.13 MiB

环境：WSL2 (Linux 6.18.33.1)，Python 3.12.3，仅标准库。
内存口径：`tracemalloc` 增量峰值（Python 堆，不含解释器常驻与输入素材本身），
另附进程 RSS 峰值增量（`/proc/self/status` VmHWM 差值）。

实测输出（`docs/benchmark_output.txt`）：

```
平台: Linux-6.18.33.1-microsoft-standard-WSL2-x86_64-with-glibc2.39  Python 3.12.3
素材: 音频 168750 帧 + 视频 108000 帧, 时长 3600s
生成素材: 0.196s, RSS 80.62 MiB

封装(mux):
  耗时            3.331 s
  输出文件        19.13 MiB
  tracemalloc峰值 0.01 MiB
  RSS峰值增量     0.06 MiB
  报告            0 条 (时长不一致: False)

解封装(demux):
  耗时            5.341 s
  tracemalloc峰值 93.55 MiB  (含整个 MediaFile 对象图)
  RSS峰值增量     259.38 MiB

对拍: 0.049s, 不一致样本数 0
再封装字节级一致: True
```

## 要点

- **封装（mux）约 3.4 s，额外内存 ≈ 0**：轨迹本身按 dts 有序，用
  `heapq.merge` 流式归并交错 + 增量 CRC32，内存 O(轨迹数)，与素材时长无关。
- **解封装（demux）约 5.4 s**：需要把整个 `MediaFile` 对象图建回内存，
  tracemalloc 峰值 93.55 MiB（含 276750 个 `Sample` 对象与 payload）。
- 276750 个样本逐字段对拍不一致数 0；再封装字节级一致。
