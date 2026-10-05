"""Benchmark: 1,000,000 escapes per context. Run: python3 bench.py [N]

Timing pass: gc disabled, no tracemalloc (tracemalloc inflates hot loops
~6x, so memory is measured in a separate short pass).
"""
import gc
import resource
import sys
import time
import tracemalloc

from escapelib import (
    escape_attr,
    escape_css,
    escape_css_in_attr,
    escape_css_url,
    escape_js_string,
    escape_json_in_script,
    escape_text,
    escape_url,
    escape_url_in_attr,
)

N = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
MEM_N = 50_000  # peak is iteration-count independent (no accumulation)

CASES = [
    ("text", escape_text,
     '<b class="x">Tom & Jerry</b>\n' * 4),
    ("attr", escape_attr,
     '" onclick="alert(1)" x=\'y\' <tag> & ' * 4),
    ("js", escape_js_string,
     '";alert(1);//\n</script>\\' * 4),
    ("css", escape_css,
     'red;}</style>expression(alert(1)) ' * 4),
    ("url", escape_url,
     "https://example.com/a b?x=1&y=2 <tag>" * 2),
    ("url_in_attr", escape_url_in_attr,
     'https://example.com/a?x=" o="y\'' * 2),
    ("url_in_css", escape_css_url,
     'https://e.com/a (x) "y" z ' * 2),
    ("css_in_attr", escape_css_in_attr,
     'color:red" onmouseover="x(1)" ' * 2),
    ("json_in_script", escape_json_in_script,
     {"a": "</script><script>x</script>", "b": "\u2028&<>"}),
]


def time_pass(fn, payload):
    gc.disable()
    try:
        t0 = time.perf_counter()
        for _ in range(N):
            fn(payload)
        return time.perf_counter() - t0
    finally:
        gc.enable()
        gc.collect()


def mem_pass(fn, payload):
    gc.collect()
    tracemalloc.start()
    for _ in range(MEM_N):
        fn(payload)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak


if __name__ == "__main__":
    print(f"转义次数/上下文: {N:,}  (Python {sys.version.split()[0]})")
    print(f"{'上下文':<14}{'耗时':>10}{'吞吐':>10}{'内存峰值(tracemalloc)':>22}")
    print("-" * 60)
    for label, fn, payload in CASES:
        elapsed = time_pass(fn, payload)
        peak = mem_pass(fn, payload)
        print(
            f"{label:<14}{elapsed:>8.2f} s"
            f"{N / elapsed / 1e6:>8.2f} M/s"
            f"{peak / 1024:>16.1f} KiB"
        )
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print("-" * 60)
    print(f"进程 RSS 峰值 (ru_maxrss): {rss_kb / 1024:.1f} MiB")
