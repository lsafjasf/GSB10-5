#!/usr/bin/env python3
"""selftest.py — channelops 自测与压测（仅标准库）。

运行：python3 selftest.py
输出：控制台报告 + RESULTS.md
"""

import hashlib
import random
import resource
import sys
import time
import tracemalloc

import channelops as co

ORDERS = ["RGB", "BGR", "RGBA", "BGRA", "ARGB", "ABGR"]
BIT_DEPTHS = [8, 16]
BYTE_ORDERS = ["big", "little"]

report_lines = []


def emit(line=""):
    print(line)
    report_lines.append(line)


def make_image(rng, width, height, order, bit_depth, byte_order):
    """生成确定性随机测试图（交错字节）。"""
    n = width * height * len(order)
    if bit_depth == 8:
        return bytes(rng.getrandbits(8) for _ in range(n))
    fmt = "big" if byte_order == "big" else "little"
    out = bytearray(n * 2)
    for i in range(n):
        out[i * 2:(i + 1) * 2] = rng.getrandbits(16).to_bytes(2, fmt)
    return bytes(out)


def samples_of(plane, bit_depth, byte_order):
    return list(co._sample_iter(plane, bit_depth // 8, byte_order))


# ---------------------------------------------------------------------------
# 1. 通道顺序 × 位深 组合矩阵
# ---------------------------------------------------------------------------

def test_matrix():
    emit("## 1. 通道顺序 × 位深 组合矩阵（逐项验证）")
    emit("")
    emit("| 通道顺序 | 位深 | 字节序 | 平面内容校验 | 合并字节校验 | 结果 |")
    emit("|---|---|---|---|---|---|")
    rng = random.Random(20261005)
    all_ok = True
    for order in ORDERS:
        for depth in BIT_DEPTHS:
            for bo in BYTE_ORDERS:
                w, h = 17, 13  # 非对齐尺寸，防止碰巧整除掩盖错位
                data = make_image(rng, w, h, order, depth, bo)
                planes = co.split_channels(data, w, h, order, depth, bo)
                bps = depth // 8
                nch = len(order)

                # 逐像素核对：planes[name][i] 必须等于原图第 i 像素对应通道样本
                plane_vals = {k: samples_of(v, depth, bo)
                              for k, v in planes.items()}
                raw = [int.from_bytes(data[i:i + bps], bo)
                       for i in range(0, len(data), bps)]
                content_ok = True
                for ci, name in enumerate(order):
                    expect = raw[ci::nch]
                    if plane_vals[name] != expect:
                        content_ok = False
                        break

                merged = co.merge_channels(planes, w, h, order, depth, bo)
                merge_ok = (merged == data)
                ok = content_ok and merge_ok
                all_ok &= ok
                emit("| %s | %d | %s | %s | %s | %s |"
                     % (order, depth, bo,
                        "通过" if content_ok else "失败",
                        "通过" if merge_ok else "失败",
                        "PASS" if ok else "FAIL"))
    emit("")
    emit("矩阵结论：%d 种组合全部 %s。"
         % (len(ORDERS) * len(BIT_DEPTHS) * len(BYTE_ORDERS),
            "通过" if all_ok else "存在失败"))
    emit("")
    return all_ok


# ---------------------------------------------------------------------------
# 2. 分离-合并往返对拍（逐字节）
# ---------------------------------------------------------------------------

def test_roundtrip():
    emit("## 2. 分离 → 合并 往返对拍（逐字节一致）")
    emit("")
    emit("| 通道顺序 | 位深 | 图片数 | 尺寸 | 逐字节一致 | 原图 SHA-256(前16) | 合并 SHA-256(前16) |")
    emit("|---|---|---|---|---|---|---|")
    rng = random.Random(7)
    all_ok = True
    for order in ORDERS:
        for depth in BIT_DEPTHS:
            count = 200
            h_src = hashlib.sha256()
            h_merged = hashlib.sha256()
            ok = True
            for t in range(count):
                w = rng.randint(1, 64)
                h = rng.randint(1, 64)
                bo = BYTE_ORDERS[t % 2]
                data = make_image(rng, w, h, order, depth, bo)
                planes = co.split_channels(data, w, h, order, depth, bo)
                merged = co.merge_channels(planes, w, h, order, depth, bo)
                if merged != data:
                    ok = False
                h_src.update(data)
                h_merged.update(merged)
            same_hash = h_src.hexdigest() == h_merged.hexdigest()
            ok = ok and same_hash
            all_ok &= ok
            emit("| %s | %d | %d | 随机 1~64px | %s | %s | %s |"
                 % (order, depth, count,
                    "是(%d/%d)" % (count, count) if ok else "否",
                    h_src.hexdigest()[:16], h_merged.hexdigest()[:16]))
    emit("")
    emit("往返结论：共 %d 张图，分离再合并与原图逐字节一致：%s。"
         % (200 * len(ORDERS) * len(BIT_DEPTHS),
            "全部一致" if all_ok else "存在不一致"))
    emit("")
    return all_ok


# ---------------------------------------------------------------------------
# 3. 不匹配拒绝（通道数 / 位深），要求指出具体位置
# ---------------------------------------------------------------------------

def test_rejection():
    emit("## 3. 通道数不一致 / 位深不匹配 的拒绝行为")
    emit("")
    w, h = 4, 3
    rng = random.Random(1)
    data = make_image(rng, w, h, "RGBA", 8, "big")
    planes = co.split_channels(data, w, h, "RGBA", 8, "big")
    results = []

    # 3.1 少一个平面（缺 A）
    p = dict(planes); del p["A"]
    try:
        co.merge_channels(p, w, h, "RGBA", 8)
        results.append(("缺少平面 A", "未拒绝", False, ""))
    except co.ChannelCountMismatch as e:
        results.append(("缺少平面 A", "已拒绝 ChannelCountMismatch", True, str(e)))

    # 3.2 多一个平面（RGB 数据塞进 RGBA）
    p = dict(planes); p["X"] = planes["R"]
    try:
        co.merge_channels(p, w, h, "RGBA", 8)
        results.append(("多余平面 X", "未拒绝", False, ""))
    except co.ChannelCountMismatch as e:
        results.append(("多余平面 X", "已拒绝 ChannelCountMismatch", True, str(e)))

    # 3.3 用 RGB 平面集合冒充 RGBA
    p = {k: planes[k] for k in "RGB"}
    try:
        co.merge_channels(p, w, h, "RGBA", 8)
        results.append(("3 平面 vs RGBA 4 通道", "未拒绝", False, ""))
    except co.ChannelCountMismatch as e:
        results.append(("3 平面 vs RGBA 4 通道", "已拒绝 ChannelCountMismatch", True, str(e)))

    # 3.4 位深不匹配：8bit 图按 16bit 合并（平面长度减半）
    p8 = co.split_channels(data, w, h, "RGBA", 8)
    try:
        co.merge_channels(p8, w, h, "RGBA", 16)
        results.append(("8bit 平面按 16bit 合并", "未拒绝", False, ""))
    except co.BitDepthMismatch as e:
        results.append(("8bit 平面按 16bit 合并", "已拒绝 BitDepthMismatch", True, str(e)))

    # 3.5 单个平面被截断（指出是哪个平面、第几位）
    p = dict(planes); p["G"] = planes["G"][:-1]
    try:
        co.merge_channels(p, w, h, "RGBA", 8)
        results.append(("G 平面短 1 字节", "未拒绝", False, ""))
    except co.BitDepthMismatch as e:
        results.append(("G 平面短 1 字节", "已拒绝 BitDepthMismatch", True, str(e)))

    # 3.6 分离时数据总长度与声明几何不符
    try:
        co.split_channels(data[:-3], w, h, "RGBA", 8)
        results.append(("数据长度与几何不符", "未拒绝", False, ""))
    except co.ChannelError as e:
        results.append(("数据长度与几何不符", "已拒绝 ChannelError", True, str(e)))

    all_ok = all(r[2] for r in results)
    for name, verdict, ok, msg in results:
        emit("- [%s] %s → %s" % ("PASS" if ok else "FAIL", name, verdict))
        if msg:
            emit("  - 错误信息：`%s`" % msg)
    emit("")
    emit("拒绝行为结论：%d 项检查全部%s。" % (len(results), "通过" if all_ok else "未通过"))
    emit("")
    return all_ok


# ---------------------------------------------------------------------------
# 4. 预乘 vs 非预乘 对照
# ---------------------------------------------------------------------------

def test_premultiply():
    emit("## 4. 含透明度：预乘（premultiplied） vs 非预乘（straight）对照")
    emit("")
    all_ok = True
    for depth in (8, 16):
        maxv = (1 << depth) - 1
        # 固定 6 个像素，覆盖 a=0、半透明、a=max
        px = [
            (maxv, 0, 0, maxv),                 # 不透明红
            (maxv, 0, 0, maxv // 2),            # 半透明红
            (0, maxv // 2, maxv, maxv // 4),    # 1/4 透明
            (maxv // 3, maxv // 3, maxv // 3, 0),  # 全透明（颜色被约定丢弃）
            (10, 200, 30, 128 if depth == 8 else 32768),
            (maxv, maxv, maxv, maxv),           # 不透明白
        ]
        w, h = 3, 2
        order = "RGBA"
        bo = "big"
        bps = depth // 8
        data = b"".join(b"".join(c.to_bytes(bps, bo) for c in p) for p in px)
        prem, stats1 = co.convert_premultiplied(data, w, h, order, depth,
                                                bo, to_premultiplied=True)
        back, stats2 = co.convert_premultiplied(prem, w, h, order, depth,
                                                bo, to_premultiplied=False)

        def px_of(buf, i):
            base = i * 4 * bps
            return tuple(int.from_bytes(buf[base + k * bps: base + (k + 1) * bps], bo)
                         for k in range(4))

        emit("### %d-bit RGBA（max=%d）" % (depth, maxv))
        emit("")
        emit("| 像素 | 非预乘 (R,G,B,A) | 预乘 (R,G,B,A) | 预乘→非预乘 还原 | 还原误差 |")
        emit("|---|---|---|---|---|")
        max_err = 0
        for i in range(6):
            s = px_of(data, i)
            p = px_of(prem, i)
            b = px_of(back, i)
            if s[3] == 0:
                err = "—（a=0 颜色按约定置 0，不可逆）"
                errv = 0
            else:
                errv = max(abs(s[k] - b[k]) for k in range(3))
                max_err = max(max_err, errv)
                err = "±%d" % errv
            emit("| %d | %s | %s | %s | %s |" % (i, s, p, b, err))
        emit("")
        emit("- a=0 像素数：%d（正向）/ %d（逆向按约定置 0）"
             % (stats1["zero_alpha_pixels"], stats2["zero_alpha_pixels"]))
        emit("- a>0 像素还原最大误差：%d（%d-bit，来自整数舍入）" % (max_err, depth))
        emit("- 结论：预乘后颜色 = 颜色 × α/%d；同一像素两种表示数值不同，"
             "混用会导致整体偏色/变暗，必须区分处理。" % maxv)
        emit("")
        # 自校验：正向结果符合公式
        for i in range(6):
            s = px_of(data, i); p = px_of(prem, i)
            for k in range(3):
                expect = (s[k] * s[3] + maxv // 2) // maxv
                if p[k] != expect:
                    all_ok = False
    return all_ok


# ---------------------------------------------------------------------------
# 5. 1 万张图 耗时 / 内存峰值
# ---------------------------------------------------------------------------

def bench(count=10000):
    emit("## 5. 压测：%d 张图的分离+合并耗时与内存峰值" % count)
    emit("")
    rng = random.Random(99)
    w, h = 256, 256
    order, depth, bo = "RGBA", 8, "big"
    img = make_image(rng, w, h, order, depth, bo)
    emit("- 单图规格：%dx%d %s %d-bit（%d 字节）" % (w, h, order, depth, len(img)))
    emit("- 流程：split_channels → merge_channels → 逐字节比对，逐张处理不驻留")
    emit("")

    tracemalloc.start()
    t0 = time.perf_counter()
    ok = True
    for _ in range(count):
        planes = co.split_channels(img, w, h, order, depth, bo)
        merged = co.merge_channels(planes, w, h, order, depth, bo)
        if merged != img:
            ok = False
    elapsed = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    emit("| 指标 | 数值 |")
    emit("|---|---|")
    emit("| 图片总数 | %d |" % count)
    emit("| 总耗时 | %.3f s |" % elapsed)
    emit("| 平均每张 | %.3f ms |" % (elapsed / count * 1000))
    emit("| 吞吐 | %.1f 张/s（%.1f MB/s）|"
         % (count / elapsed, count * len(img) / elapsed / 1e6))
    emit("| Python 堆内存峰值（tracemalloc） | %.2f MB |" % (peak / 1e6))
    emit("| 进程 RSS 峰值（ru_maxrss） | %.2f MB |" % (rss_kb / 1024))
    emit("| 往返逐字节一致 | %s |" % ("是（%d/%d）" % (count, count) if ok else "否"))
    emit("")
    return ok


def main():
    emit("# channelops 自测报告")
    emit("")
    emit("- Python：%s" % sys.version.split()[0])
    emit("- 时间：%s" % time.strftime("%Y-%m-%d %H:%M:%S %z"))
    emit("- 运行命令：`python3 selftest.py`")
    emit("")
    results = [
        ("组合矩阵", test_matrix()),
        ("往返对拍", test_roundtrip()),
        ("不匹配拒绝", test_rejection()),
        ("预乘对照", test_premultiply()),
        ("压测", bench()),
    ]
    emit("## 总结")
    emit("")
    for name, ok in results:
        emit("- %s：%s" % (name, "PASS" if ok else "FAIL"))
    all_ok = all(ok for _, ok in results)
    emit("")
    emit("总体：%s" % ("全部通过" if all_ok else "存在失败项"))
    with open("RESULTS.md", "w", encoding="utf-8") as f:
        f.write("\n".join(report_lines) + "\n")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
