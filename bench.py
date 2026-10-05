"""1 小时视频转换基准：耗时 + 内存峰值（python3 bench.py > bench.txt）。"""
import resource
import time
import tracemalloc

from frc import FrameRateConverter


def bench(name, src_fps, dst_fps, seconds=3600):
    from fractions import Fraction
    num_src = int(Fraction(seconds) * FrameRateConverter(src_fps, dst_fps, 1).src_fps)
    c = FrameRateConverter(src_fps, dst_fps, num_src)

    # 流式（逐帧处理，不驻留结果）——真实转码管线的用法
    t0 = time.perf_counter()
    n_frames = 0
    for _n, _pts, _k in c.iter_map():
        n_frames += 1
    t_stream = time.perf_counter() - t0

    # 全量物化（把映射表全部放进内存）——测内存峰值
    tracemalloc.start()
    t0 = time.perf_counter()
    table = [(n, pts, k) for n, pts, k in c.iter_map()]
    t_list = time.perf_counter() - t0
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del table

    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"{name}: 源 {num_src} 帧 -> 输出 {c.num_dst} 帧 ({seconds}s 素材)")
    print(f"  流式转换耗时:   {t_stream * 1000:8.1f} ms  "
          f"({n_frames / t_stream / 1e6:.2f} M帧/s)")
    print(f"  全量物化耗时:   {t_list * 1000:8.1f} ms")
    print(f"  物化内存峰值:   {peak / 1024 / 1024:8.1f} MiB (tracemalloc, 映射表本身)")
    print(f"  进程 RSS 峰值:  {rss_mb:8.1f} MiB (含解释器)")
    print(f"  流式模式额外内存: O(1)，逐帧产出即弃")


if __name__ == "__main__":
    print(f"机器: Python 3.12, 单线程")
    bench("24 -> 30 fps (非整数升帧)", 24, 30)
    bench("30 -> 24 fps (非整数降帧)", 30, 24)
    bench("23.976 -> 59.94 fps (NTSC 整数倍)", "24000/1001", "60000/1001")
