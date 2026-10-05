"""性能基准：每个上下文各执行 1,000,000 次转义，报告耗时与内存峰值。

运行：python3 benchmark.py
"""

import gc
import platform
import sys
import time
import tracemalloc

import secescape as esc

N = 1_000_000

# 每类上下文取一个代表性载荷（混合安全字符与需转义字符）
CASES = [
    ("text        文本内容", esc.text, "用户 <b>输入</b> & \"引号\" 混合内容"),
    ("attr        属性值", esc.attr, 'value with "quotes" & <tags> 混合'),
    ("js_string   脚本内容", esc.js_string, 'say "hi"</script><script>alert(1)</script>'),
    ("js_attr     脚本嵌套属性", esc.js_attr, '");alert(1);// 嵌套载荷'),
    ("css_string  样式内容", esc.css_string, "a'b</style><script>x</script>"),
    ("url         地址", esc.url, "https://example.com/path with space?q=1&r=2"),
    ("attr_url    地址嵌套属性", esc.attr_url, "https://example.com/a b?x=1&y=2"),
    ("css_url     地址嵌套样式", esc.css_url, "https://example.com/img a.png"),
    ("escape_once 幂等转义", esc.escape_once, "混合 &lt;实体&gt; 与 <原始> 字符 &"),
]


def bench():
    print("Python %s / %s" % (sys.version.split()[0], platform.machine()))
    print("每类上下文各转义 %s 次" % format(N, ","))
    print("-" * 72)
    print("%-26s %12s %14s %14s" % ("上下文", "总耗时(s)", "单次(ns)", "内存峰值(KB)"))
    print("-" * 72)
    total_time = 0.0
    for name, fn, payload in CASES:
        # 预热，排除首次导入/分支预测影响
        for _ in range(10_000):
            fn(payload)
        gc.collect()

        # 第一遍：纯计时（不挂 tracemalloc，避免其插桩拖慢计时）
        start = time.perf_counter()
        sink = 0
        for _ in range(N):
            sink += len(fn(payload))
        elapsed = time.perf_counter() - start

        # 第二遍：单独测内存峰值
        gc.collect()
        tracemalloc.start()
        sink = 0
        for _ in range(N):
            sink += len(fn(payload))
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        total_time += elapsed
        print("%-26s %12.3f %14.0f %14.1f" % (name, elapsed, elapsed / N * 1e9, peak / 1024))
    print("-" * 72)
    print("合计 %s 次转义，总耗时 %.3f s" % (format(N * len(CASES), ","), total_time))


if __name__ == "__main__":
    bench()
