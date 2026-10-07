"""基准: 对 100,000 条合成文本统计识别耗时与内存峰值。

合成方式(固定种子 42, 可复现): 从 12 种语言训练句中随机抽 1~3 句拼接。
内存峰值取 /proc/self/status 的 VmHWM(高水位 RSS)。
"""
import random
import sys
import time
import tracemalloc

from langid import Identifier, SUPPORTED_LANGUAGES
from train_profiles import load_split

N = 100_000


def hwm_mib() -> float:
    with open("/proc/self/status") as f:
        for ln in f:
            if ln.startswith("VmHWM"):
                return int(ln.split()[1]) / 1024
    return -1.0


def main():
    hwm0 = hwm_mib()
    idf = Identifier.load()
    hwm_model = hwm_mib()

    pool = []
    for lang in SUPPORTED_LANGUAGES:
        pool.extend(load_split(lang)[0])
    rng = random.Random(42)
    texts = [" ".join(rng.choice(pool) for _ in range(rng.choice((1, 1, 1, 2, 3))))
             for _ in range(N)]
    avg_chars = sum(len(t) for t in texts) / len(texts)
    hwm_data = hwm_mib()

    tracemalloc.start()
    t0 = time.perf_counter()
    answered = 0
    for t in texts:
        if not idf.identify(t)["rejected"]:
            answered += 1
    dt = time.perf_counter() - t0
    _, py_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    hwm1 = hwm_mib()

    print(f"texts            : {N} (avg {avg_chars:.0f} chars)")
    print(f"answered         : {answered} ({answered / N:.2%})")
    print(f"total time       : {dt:.2f} s")
    print(f"per text         : {dt / N * 1e6:.1f} us")
    print(f"throughput       : {N / dt:,.0f} texts/s")
    print(f"VmHWM baseline   : {hwm0:.1f} MiB (解释器+导入)")
    print(f"VmHWM +model     : {hwm_model:.1f} MiB "
          f"(+{hwm_model - hwm0:.1f} MiB, profiles.json)")
    print(f"VmHWM +100k输入  : {hwm_data:.1f} MiB "
          f"(+{hwm_data - hwm_model:.1f} MiB)")
    print(f"VmHWM 识别循环后 : {hwm1:.1f} MiB "
          f"(+{hwm1 - hwm_data:.1f} MiB, 识别本身增量)")
    print(f"python heap peak : {py_peak / 1024 / 1024:.2f} MiB "
          f"(tracemalloc, 循环期间)")
    print(f"python           : {sys.version.split()[0]}")


if __name__ == "__main__":
    main()
