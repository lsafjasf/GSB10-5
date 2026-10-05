"""channel_lib 自测：组合矩阵 / 往返对拍 / 拒绝路径 / 预乘对照 / 性能。

运行: python3 selftest.py
"""
from __future__ import annotations

import hashlib
import random
import resource
import sys
import time
import tracemalloc

from channel_lib import (
    SUPPORTED_BIT_DEPTHS,
    SUPPORTED_ORDERS,
    ChannelError,
    alpha_index,
    merge,
    split,
)

W, H = 37, 23  # 故意用非 2 的幂尺寸，暴露步长/对齐错误


def make_image(w, h, order, bit_depth, seed):
    n = w * h * len(order) * (bit_depth // 8)
    return random.Random(seed).randbytes(n)


def digest(b: bytes) -> str:
    return hashlib.sha1(b).hexdigest()[:12]


def test_matrix():
    print("=" * 78)
    print("1. 通道顺序 x 位深 组合矩阵（每项：分离->合并 往返对拍）")
    print("=" * 78)
    hdr = f"{'order':<5} {'bits':>4} {'endian':<7} {'img_bytes':>9} {'planes':>6} {'plane_bytes':>11} {'sha1(原图)':<13} {'往返一致':<8} 结果"
    print(hdr)
    print("-" * len(hdr))
    all_pass = True
    for order in SUPPORTED_ORDERS:
        for bit_depth in SUPPORTED_BIT_DEPTHS:
            endians = ("little", "big") if bit_depth == 16 else ("-",)
            for endian in endians:
                e = "little" if endian == "-" else endian
                data = make_image(W, H, order, bit_depth, seed=hash((order, bit_depth, endian)) & 0xFFFF)
                planes = split(W, H, order, bit_depth, data, endian=e)
                back = merge(W, H, order, bit_depth, planes, endian=e)
                ok = back == data
                all_pass &= ok
                bps = bit_depth // 8
                plane_sizes = ",".join(str(len(p)) for p in planes)
                print(f"{order:<5} {bit_depth:>4} {endian:<7} {len(data):>9} "
                      f"{len(planes):>6} {plane_sizes:>11} {digest(data):<13} "
                      f"{'YES' if ok else 'NO':<8} {'PASS' if ok else 'FAIL'}")
    print(f"\n矩阵合计 {len(SUPPORTED_ORDERS) * (len(SUPPORTED_BIT_DEPTHS) + 1)} 项，"
          f"{'全部 PASS' if all_pass else '存在 FAIL'}")
    return all_pass


def test_roundtrip_bytes():
    print()
    print("=" * 78)
    print("2. 往返逐字节对拍（split->merge 与原图逐字节比较，含首个/末个差异位置）")
    print("=" * 78)
    all_pass = True
    for order in SUPPORTED_ORDERS:
        for bit_depth in SUPPORTED_BIT_DEPTHS:
            data = make_image(W, H, order, bit_depth, seed=7)
            back = merge(W, H, order, bit_depth, split(W, H, order, bit_depth, data))
            diffs = [i for i, (x, y) in enumerate(zip(data, back)) if x != y]
            if len(back) != len(data):
                diffs.append(-1)
            ok = not diffs
            all_pass &= ok
            where = "无差异" if ok else f"首个差异@{diffs[0]}"
            print(f"{order:<5} {bit_depth:>2}bit  字节数={len(data):>6}  "
                  f"sha1(原图)={digest(data)}  sha1(往返)={digest(back)}  "
                  f"差异字节数={len(diffs)} ({where})  {'PASS' if ok else 'FAIL'}")
    print(f"\n往返对拍 {'全部 PASS（逐字节一致）' if all_pass else '存在 FAIL'}")
    return all_pass


def test_reject():
    print()
    print("=" * 78)
    print("3. 不匹配拒绝（必须抛出 ChannelError 并指出具体位置）")
    print("=" * 78)
    data8 = make_image(W, H, "RGBA", 8, seed=1)
    planes8 = split(W, H, "RGBA", 8, data8)
    cases = [
        ("通道数不足: RGBA 只给 3 个平面",
         lambda: merge(W, H, "RGBA", 8, planes8[:3])),
        ("通道数过多: RGB 给 4 个平面",
         lambda: merge(W, H, "RGB", 8, planes8)),
        ("位深不匹配: 8bit 平面按 16bit 合并",
         lambda: merge(W, H, "RGBA", 16, planes8)),
        ("位深不匹配: 16bit 平面按 8bit 合并",
         lambda: merge(W, H, "RGB", 8, split(W, H, "RGB", 16, make_image(W, H, "RGB", 16, seed=2)))),
        ("数据长度错误: split 输入少 1 字节",
         lambda: split(W, H, "RGBA", 8, data8[:-1])),
        ("单个平面长度错误: 第 2 平面被截断",
         lambda: merge(W, H, "RGBA", 8, [planes8[0], planes8[1][:-5], planes8[2], planes8[3]])),
        ("非法位深: 12bit",
         lambda: split(W, H, "RGB", 12, b"\0" * (W * H * 3 * 2))),
        ("非法通道顺序: 'RGBA' 写成 'RGBX'",
         lambda: split(W, H, "RGBX", 8, data8)),
    ]
    all_pass = True
    for name, fn in cases:
        try:
            fn()
        except ChannelError as exc:
            print(f"[PASS] {name}\n       -> ChannelError: {exc}")
        except Exception as exc:  # noqa: BLE001
            all_pass = False
            print(f"[FAIL] {name}\n       -> 抛出了非 ChannelError: {type(exc).__name__}: {exc}")
        else:
            all_pass = False
            print(f"[FAIL] {name}\n       -> 未抛出异常（错误被静默放过）")
    print(f"\n拒绝路径 {'全部 PASS' if all_pass else '存在 FAIL'}")
    return all_pass


def _pixels(data, order, bit_depth, count=4):
    """取前 count 个像素的通道值元组，用于对照展示。"""
    n = len(order)
    step = n * (bit_depth // 8)
    out = []
    for i in range(count):
        px = []
        for c in range(n):
            off = i * step + c * (bit_depth // 8)
            px.append(int.from_bytes(data[off:off + bit_depth // 8], "little"))
        out.append(tuple(px))
    return out


def test_premultiply():
    print()
    print("=" * 78)
    print("4. 含透明度：预乘 vs 非预乘 对照")
    print("=" * 78)
    all_pass = True
    for order in ("RGBA", "BGRA", "ARGB", "ABGR"):
        for bit_depth in SUPPORTED_BIT_DEPTHS:
            maxval = (1 << bit_depth) - 1
            data = make_image(W, H, order, bit_depth, seed=99)

            straight = merge(W, H, order, bit_depth,
                             split(W, H, order, bit_depth, data, premultiplied=False))
            ok_straight = straight == data

            pm_planes = split(W, H, order, bit_depth, data, premultiplied=True)
            pm_back = merge(W, H, order, bit_depth, pm_planes, premultiplied=True)
            diffs = sum(1 for x, y in zip(data, pm_back) if x != y)
            max_delta = max((abs(x - y) for x, y in zip(data, pm_back)), default=0)

            # 预乘平面本身应与原图颜色平面不同（alpha<max 的像素被压暗）
            raw_planes = split(W, H, order, bit_depth, data)
            a = alpha_index(order)
            changed = sum(1 for c in range(len(order)) if c != a and pm_planes[c] != raw_planes[c])

            all_pass &= ok_straight
            print(f"\n[{order} {bit_depth}bit] alpha 通道下标={a} maxval={maxval}")
            print(f"  非预乘往返: {'逐字节一致 PASS' if ok_straight else '不一致 FAIL'}")
            print(f"  预乘往返:   差异字节={diffs}/{len(data)}  最大单字节偏差={max_delta}"
                  f"  （预乘是有损量化，差异符合预期）")
            print(f"  预乘后颜色平面与原平面不同的通道数: {changed}/{len(order) - 1}")
            print(f"  前 4 像素 原图:      {_pixels(data, order, bit_depth)}")
            print(f"  前 4 像素 预乘往返:  {_pixels(pm_back, order, bit_depth)}")
    print(f"\n非预乘路径 {'全部逐字节一致 PASS' if all_pass else '存在 FAIL'}；"
          f"预乘路径按设计存在量化误差，已逐项列出。")
    return all_pass


def test_perf():
    print()
    print("=" * 78)
    print("5. 性能：10,000 张图 分离+合并 耗时与内存峰值")
    print("=" * 78)
    configs = [
        ("RGBA", 8, 256, 256),
        ("RGB", 8, 256, 256),
        ("RGBA", 16, 256, 256),
    ]
    all_pass = True
    for order, bit_depth, w, h in configs:
        count = 10_000
        rng = random.Random(2026)
        nbytes = w * h * len(order) * (bit_depth // 8)
        images = [rng.randbytes(nbytes) for _ in range(count)]

        tracemalloc.start()
        t0 = time.perf_counter()
        ok = True
        for img in images:
            back = merge(w, h, order, bit_depth, split(w, h, order, bit_depth, img))
            ok = ok and (back == img)
        dt = time.perf_counter() - t0
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        mpix = w * h * count / 1e6
        all_pass &= ok
        print(f"[{order} {bit_depth}bit {w}x{h} x {count} 张]")
        print(f"  往返全部逐字节一致: {'YES' if ok else 'NO'}")
        print(f"  总耗时: {dt:.2f}s   平均每张: {dt / count * 1e3:.3f}ms   "
              f"吞吐: {mpix / dt:.1f} Mpix/s ({count * nbytes / dt / 2**20:.0f} MiB/s)")
        print(f"  内存峰值(tracemalloc): {peak / 2**20:.1f} MiB   "
              f"进程峰值 RSS(ru_maxrss): {rss_kb / 1024:.1f} MiB")
        del images
    print(f"\n性能测试 {'全部 PASS' if all_pass else '存在 FAIL'}")
    return all_pass


def main():
    print(f"Python {sys.version.split()[0]}  platform={sys.platform}  byteorder={sys.byteorder}")
    results = [
        ("组合矩阵", test_matrix()),
        ("往返对拍", test_roundtrip_bytes()),
        ("不匹配拒绝", test_reject()),
        ("预乘对照", test_premultiply()),
        ("性能", test_perf()),
    ]
    print()
    print("=" * 78)
    print("汇总")
    print("=" * 78)
    for name, ok in results:
        print(f"  {name:<8} {'PASS' if ok else 'FAIL'}")
    ok = all(r for _, r in results)
    print(f"\n总体: {'ALL PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
