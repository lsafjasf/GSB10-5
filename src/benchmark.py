"""Throughput benchmark: 10,000 images, 128x128, pure stdlib.

Streams images (generate -> denoise -> discard) so peak memory reflects
steady-state processing, not dataset size.  Reports wall time per method
and peak RSS (ru_maxrss) + tracemalloc Python-heap peak.
"""

import os
import resource
import sys
import time
import tracemalloc

import denoise as dn

N = 10000
SIZE = 128


def rss_kb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else N
    methods = [
        ("box r=2 (5x5)", lambda p, w, h: dn.box_filter(p, w, h, 2)),
        ("median r=1 (3x3)", lambda p, w, h: dn.median_filter(p, w, h, 1)),
        ("selective r=2 T=2.5*sigma",
         lambda p, w, h: dn.selective_mean(p, w, h, 2,
                                           2.5 * dn.estimate_sigma(p, w, h))),
    ]
    print("benchmark: %d images, %dx%d grayscale" % (n, SIZE, SIZE))
    print("baseline RSS: %.1f MB" % (rss_kb() / 1024))
    t_gen = 0.0
    results = []
    for label, fn in methods:
        t0 = time.perf_counter()
        checksum = 0
        for i in range(n):
            tg = time.perf_counter()
            clean, w, h = dn.make_image(SIZE, SIZE, "edges")
            noisy = dn.add_gaussian_noise(clean, 25.0, seed=i)
            t_gen += time.perf_counter() - tg
            out = fn(noisy, w, h)
            checksum = (checksum + out[0] + out[-1]) & 0xFFFFFFFF
        dt = time.perf_counter() - t0
        # Python-heap peak measured separately: tracemalloc slows the
        # timed loop ~30x, so profile a short untimed run instead.
        tracemalloc.start()
        for i in range(50):
            clean, w, h = dn.make_image(SIZE, SIZE, "edges")
            noisy = dn.add_gaussian_noise(clean, 25.0, seed=i)
            fn(noisy, w, h)
        _, py_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        results.append((label, dt, n / dt, rss_kb() / 1024,
                        py_peak / 1024 / 1024, checksum))
        print("%-28s %8.1f s  %7.1f img/s  peakRSS %6.1f MB  "
              "py-peak %5.1f MB  (checksum %08x)" % results[-1])
    print("input generation time (shared): %.1f s" % t_gen)
    print("final RSS: %.1f MB" % (rss_kb() / 1024))
    out_path = os.path.join(os.path.dirname(__file__), "..", "benchmark.md")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("# Benchmark: %d images, %dx%d, Python %s, pure stdlib\n\n"
                 % (n, SIZE, SIZE, sys.version.split()[0]))
        fh.write("| method | total (s) | img/s | ms/img | peak RSS (MB) "
                 "| py heap peak (MB) |\n|---|---|---|---|---|---|\n")
        for label, dt, ips, rss, pyp, _ in results:
            fh.write("| %s | %.1f | %.1f | %.2f | %.1f | %.1f |\n"
                     % (label, dt, ips, dt / n * 1e3, rss, pyp))
        fh.write("\nInput synthesis (shared by all methods): %.1f s. "
                 "Images are streamed (generate -> denoise -> discard), "
                 "so peak memory is steady-state, independent of count.\n"
                 % t_gen)
    print("written:", os.path.abspath(out_path))


if __name__ == "__main__":
    main()
