# Benchmark: 10000 images, 128x128, Python 3.12.3, pure stdlib

| method | total (s) | img/s | ms/img | peak RSS (MB) | py heap peak (MB) |
|---|---|---|---|---|---|
| box r=2 (5x5) | 108.7 | 92.0 | 10.87 | 15.1 | 1.0 |
| median r=1 (3x3) | 152.8 | 65.5 | 15.28 | 15.1 | 0.4 |
| selective r=2 T=2.5*sigma | 466.3 | 21.4 | 46.63 | 15.1 | 0.4 |

Input synthesis (shared by all methods): 192.7 s. Images are streamed (generate -> denoise -> discard), so peak memory is steady-state, independent of count.
