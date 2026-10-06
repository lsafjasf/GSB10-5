# Experiment results

Source 512x384, box 160x120, unsharp amount 0.8, threshold 2; ground truth = same analytic scene rendered at output size with 6x6 box supersampling.

## Sharpen order vs ground truth

| scene | pipeline | SSIM | PSNR(dB) | MAE | edge energy | PNG bytes |
|---|---|---|---|---|---|---|
| texture | sharpen -> resize | 0.9920 | 28.81 | 7.45 | 34.86 (GT 41.62) | 29795 |
| texture | resize -> sharpen | 0.9977 | 27.74 | 8.56 | 43.35 (GT 41.62) | 30727 |
| texture | resize only (baseline) | 0.9903 | 31.02 | 5.92 | 33.78 (GT 41.62) | 27656 |
| photo | sharpen -> resize | 0.9942 | 30.79 | 5.66 | 1.13 (GT 1.06) | 42621 |
| photo | resize -> sharpen | 0.9934 | 30.77 | 5.68 | 1.15 (GT 1.06) | 39280 |
| photo | resize only (baseline) | 0.9972 | 51.66 | 0.44 | 1.07 (GT 1.06) | 36768 |
| edges | sharpen -> resize | 0.9019 | 21.90 | 12.30 | 3.73 (GT 4.14) | 1171 |
| edges | resize -> sharpen | 0.9105 | 21.81 | 12.59 | 4.37 (GT 4.14) | 1682 |
| edges | resize only (baseline) | 0.9072 | 29.44 | 2.66 | 3.79 (GT 4.14) | 1126 |

## Resampling kernel vs ground truth (no sharpen)

| scene | kernel | SSIM | PSNR(dB) | MAE |
|---|---|---|---|---|
| texture | nearest | 0.9767 | 25.93 | 10.43 |
| texture | box | 0.9953 | 32.83 | 4.44 |
| texture | triangle | 0.9903 | 31.02 | 5.92 |
| texture | lanczos3 | 0.9940 | 31.94 | 5.25 |
| photo | nearest | 0.9973 | 52.07 | 0.40 |
| photo | box | 0.9972 | 51.55 | 0.45 |
| photo | triangle | 0.9972 | 51.66 | 0.44 |
| photo | lanczos3 | 0.9973 | 51.94 | 0.42 |
| edges | nearest | 0.7973 | 24.14 | 3.06 |
| edges | box | 0.9129 | 29.85 | 1.88 |
| edges | triangle | 0.9072 | 29.44 | 2.66 |
| edges | lanczos3 | 0.9020 | 29.82 | 2.83 |

## Deterministic sizing (min_side=8, hard_max=8192)

| case | src | box | output | aspect delta % |
|---|---|---|---|---|
| normal 4:3 -> 160x120 box | 512x384 | 160x120 | 160x120 | 0.000 |
| ultra-wide 20:1 | 2000x100 | 160x160 | 160x8 | 0.000 |
| ultra-tall 1:20 | 120x2400 | 160x160 | 8x160 | 0.000 |
| very long ribbon 500:1 | 4000x8 | 256x256 | 4000x8 | 0.000 |
| vertical ribbon 1:500 | 8x4000 | 128x128 | 8x4000 | 0.000 |
| explicit 1x1 target | 512x384 | 1x1 | 1x1 | 25.000 |
| explicit 3x2 target | 512x384 | 3x2 | 3x2 | 12.500 |
| explicit 8x8 target | 512x384 | 8x8 | 11x8 | 3.125 |
