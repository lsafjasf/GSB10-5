# Denoising experiment results (128x128, 0-255)

Paired metrics per method: **noise side** = PSNR over flat (non-edge) pixels; **detail side** = PSNR over edge/texture pixels, PSNR over the dilated edge band (edge neighbourhood), and gradient-energy ratio over edge pixels (1.0 = full retention, <1 = detail lost).

## light_noise (scene=edges, gaussian level=10.0)

sigma_true=10.0, sigma_est(MAD)=10.5

| method | flat PSNR (dB) | edge PSNR (dB) | edge-band PSNR (dB) | grad ratio |
|---|---|---|---|---|
| (no denoise) | 28.26 -> 28.26 | 28.32 -> 28.32 | 28.19 -> 28.19 | 1.067 |
| box r=1 (3x3) | 28.26 -> 28.92 | 28.32 -> 13.50 | 28.19 -> 15.19 | 0.347 |
| box r=2 (5x5) | 28.26 -> 26.14 | 28.32 -> 11.90 | 28.19 -> 13.12 | 0.208 |
| box r=3 (7x7) | 28.26 -> 24.18 | 28.32 -> 11.28 | 28.19 -> 12.15 | 0.148 |
| median r=1 (3x3) | 28.26 -> 34.45 | 28.32 -> 24.55 | 28.19 -> 25.93 | 0.924 |
| median r=2 (5x5) | 28.26 -> 34.48 | 28.32 -> 20.57 | 28.19 -> 21.97 | 0.854 |
| selective r=2, T=1.5*sigma (T=15) | 28.26 -> 32.62 | 28.32 -> 31.96 | 28.19 -> 32.10 | 1.038 |
| selective r=2, T=2.5*sigma (T=25) | 28.26 -> 37.84 | 28.32 -> 36.28 | 28.19 -> 36.61 | 1.018 |
| selective r=2, T=3.5*sigma (T=35) | 28.26 -> 40.18 | 28.32 -> 37.58 | 28.19 -> 38.49 | 1.011 |

## heavy_noise (scene=edges, gaussian level=45.0)

sigma_true=45.0, sigma_est(MAD)=39.8

| method | flat PSNR (dB) | edge PSNR (dB) | edge-band PSNR (dB) | grad ratio |
|---|---|---|---|---|
| (no denoise) | 16.13 -> 16.13 | 16.65 -> 16.65 | 16.40 -> 16.40 | 1.208 |
| box r=1 (3x3) | 16.13 -> 23.56 | 16.65 -> 13.19 | 16.40 -> 14.64 | 0.367 |
| box r=2 (5x5) | 16.13 -> 23.90 | 16.65 -> 11.84 | 16.40 -> 12.87 | 0.214 |
| box r=3 (7x7) | 16.13 -> 22.92 | 16.65 -> 11.26 | 16.40 -> 12.01 | 0.149 |
| median r=1 (3x3) | 16.13 -> 22.48 | 16.65 -> 17.01 | 16.40 -> 17.95 | 0.730 |
| median r=2 (5x5) | 16.13 -> 25.38 | 16.65 -> 15.53 | 16.40 -> 16.59 | 0.537 |
| selective r=2, T=1.5*sigma (T=67) | 16.13 -> 20.70 | 16.65 -> 19.18 | 16.40 -> 19.16 | 1.064 |
| selective r=2, T=2.5*sigma (T=112) | 16.13 -> 25.42 | 16.65 -> 19.69 | 16.40 -> 20.21 | 0.916 |
| selective r=2, T=3.5*sigma (T=157) | 16.13 -> 26.14 | 16.65 -> 16.85 | 16.40 -> 17.66 | 0.728 |

## fine_texture (scene=texture, gaussian level=18.0)

sigma_true=18.0, sigma_est(MAD)=68.1

| method | flat PSNR (dB) | edge PSNR (dB) | edge-band PSNR (dB) | grad ratio |
|---|---|---|---|---|
| (no denoise) | 23.23 -> 23.23 | 23.25 -> 23.25 | 23.24 -> 23.24 | 1.048 |
| box r=1 (3x3) | 23.23 -> 10.92 | 23.25 -> 10.77 | 23.24 -> 10.81 | 0.126 |
| box r=2 (5x5) | 23.23 -> 10.00 | 23.25 -> 10.08 | 23.24 -> 10.06 | 0.048 |
| box r=3 (7x7) | 23.23 -> 9.90 | 23.25 -> 9.92 | 23.24 -> 9.91 | 0.029 |
| median r=1 (3x3) | 23.23 -> 20.58 | 23.25 -> 20.34 | 23.24 -> 20.40 | 0.751 |
| median r=2 (5x5) | 23.23 -> 13.47 | 23.25 -> 16.15 | 23.24 -> 15.26 | 0.612 |
| selective r=2, T=1.5*sigma (T=27) | 23.23 -> 26.42 | 23.25 -> 26.71 | 23.24 -> 26.63 | 1.023 |
| selective r=2, T=2.5*sigma (T=45) | 23.23 -> 30.72 | 23.25 -> 31.11 | 23.24 -> 31.00 | 1.006 |
| selective r=2, T=3.5*sigma (T=63) | 23.23 -> 32.72 | 23.25 -> 33.22 | 23.24 -> 33.08 | 1.000 |

## impulse+edges (scene=edges, impulse level=0.12)

impulse noise: sigma_est(MAD)=0.0 (Gaussian model does not apply)

| method | flat PSNR (dB) | edge PSNR (dB) | edge-band PSNR (dB) | grad ratio |
|---|---|---|---|---|
| (no denoise) | 13.73 -> 13.73 | 14.59 -> 14.59 | 13.81 -> 13.81 | 1.092 |
| box r=1 (3x3) | 13.73 -> 21.35 | 14.59 -> 12.98 | 13.81 -> 14.17 | 0.354 |
| box r=2 (5x5) | 13.73 -> 22.31 | 14.59 -> 11.59 | 13.81 -> 12.55 | 0.205 |
| box r=3 (7x7) | 13.73 -> 21.81 | 14.59 -> 10.95 | 13.81 -> 11.78 | 0.144 |
| median r=1 (3x3) | 13.73 -> 33.50 | 14.59 -> 19.94 | 13.81 -> 20.22 | 0.959 |
| median r=2 (5x5) | 13.73 -> 32.65 | 14.59 -> 17.47 | 13.81 -> 18.69 | 0.927 |
| selective r=2, T=30 (fixed) | 13.73 -> 13.73 | 14.59 -> 14.60 | 13.81 -> 13.82 | 1.091 |

