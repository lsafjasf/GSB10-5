# 自测报告

图像 320x240，留白 margin=12%，比例容差 1%。

## 主体判据计算结果

| 图 | 检出 | 边缘密度 | 显著度均值±标准差 | 阈值 | 前景显著度 | 背景显著度 | 分离比 | 凝聚度 | 主体bbox | 未检出原因 |
|---|---|---|---|---|---|---|---|---|---|---|
| center_square | 是 | 0.030 | 3.11±12.47 | 21.80 | 61.2 | 0.8 | 73.90 | 0.74 | [125, 85, 195, 155] | - |
| edge_left | 是 | 0.023 | 1.32±6.01 | 18.00 | 38.5 | 0.5 | 85.01 | 0.67 | [0, 55, 35, 185] | - |
| edge_corner | 是 | 0.013 | 0.71±4.42 | 18.00 | 37.6 | 0.2 | 158.38 | 0.67 | [0, 0, 55, 55] | - |
| edge_right | 是 | 0.014 | 0.85±5.15 | 18.00 | 38.7 | 0.2 | 151.91 | 0.71 | [300, 75, 320, 165] | - |
| panoramic_fullwidth | 是 | 0.083 | 6.51±14.60 | 28.40 | 51.7 | 4.5 | 11.37 | 0.33 | [0, 95, 320, 105] | - |
| flat_none | 否 | 0.000 | 0.00±0.00 | 18.00 | 0.0 | 0.0 | 0.00 | 0.00 | [0, 0, 0, 0] | edge_density<0.005、component_area<8px、coherence<0.18、separation<1.80 |
| noise_none | 否 | 0.913 | 55.13±25.21 | 92.95 | 112.0 | 54.5 | 2.05 | 0.02 | [125, 80, 180, 125] | coherence<0.18 |
| faint_subject | 否 | 0.000 | 0.24±0.94 | 18.00 | 0.0 | 0.2 | 0.00 | 0.00 | [0, 0, 0, 0] | edge_density<0.005、component_area<8px、coherence<0.18、separation<1.80 |

## 裁剪框与越界校验（contain 策略）

| 图 | 目标比例 | 决策 | 裁剪框(x0,y0,x1,y1) | 实际比例 | 比例误差 | 主体完整 | 越界校验 |
|---|---|---|---|---|---|---|---|
| center_square | 1:1 | cropped/contain | [40, 0, 280, 240] | 1.0000 | 0.0000% | 是 | PASS |
| center_square | 16:9 | cropped/contain | [0, 30, 320, 210] | 1.7778 | 0.0000% | 是 | PASS |
| center_square | 9:16 | cropped/contain | [93, 0, 228, 240] | 0.5625 | 0.0000% | 是 | PASS |
| center_square | 4:1_extreme | cropped/contain_partial | [0, 80, 320, 160] | 4.0000 | 0.0000% | 否(部分) | PASS |
| center_square | 1:4_extreme | cropped/contain_partial | [130, 0, 190, 240] | 0.2500 | 0.0000% | 否(部分) | PASS |
| edge_left | 1:1 | cropped/contain | [0, 0, 240, 240] | 1.0000 | 0.0000% | 是 | PASS |
| edge_left | 16:9 | cropped/contain | [0, 30, 320, 210] | 1.7778 | 0.0000% | 是 | PASS |
| edge_left | 9:16 | cropped/contain | [0, 0, 135, 240] | 0.5625 | 0.0000% | 是 | PASS |
| edge_left | 4:1_extreme | cropped/contain_partial | [0, 80, 320, 160] | 4.0000 | 0.0000% | 否(部分) | PASS |
| edge_left | 1:4_extreme | cropped/contain | [0, 0, 60, 240] | 0.2500 | 0.0000% | 是 | PASS |
| edge_corner | 1:1 | cropped/contain | [0, 0, 240, 240] | 1.0000 | 0.0000% | 是 | PASS |
| edge_corner | 16:9 | cropped/contain | [0, 0, 320, 180] | 1.7778 | 0.0000% | 是 | PASS |
| edge_corner | 9:16 | cropped/contain | [0, 0, 135, 240] | 0.5625 | 0.0000% | 是 | PASS |
| edge_corner | 4:1_extreme | cropped/contain | [0, 0, 320, 80] | 4.0000 | 0.0000% | 是 | PASS |
| edge_corner | 1:4_extreme | cropped/contain_partial | [0, 0, 60, 240] | 0.2500 | 0.0000% | 否(部分) | PASS |
| edge_right | 1:1 | cropped/contain | [80, 0, 320, 240] | 1.0000 | 0.0000% | 是 | PASS |
| edge_right | 16:9 | cropped/contain | [0, 30, 320, 210] | 1.7778 | 0.0000% | 是 | PASS |
| edge_right | 9:16 | cropped/contain | [185, 0, 320, 240] | 0.5625 | 0.0000% | 是 | PASS |
| edge_right | 4:1_extreme | cropped/contain_partial | [0, 80, 320, 160] | 4.0000 | 0.0000% | 否(部分) | PASS |
| edge_right | 1:4_extreme | cropped/contain | [260, 0, 320, 240] | 0.2500 | 0.0000% | 是 | PASS |
| panoramic_fullwidth | 1:1 | cropped/contain_partial | [40, 0, 280, 240] | 1.0000 | 0.0000% | 否(部分) | PASS |
| panoramic_fullwidth | 16:9 | cropped/contain_partial | [0, 10, 320, 190] | 1.7778 | 0.0000% | 否(部分) | PASS |
| panoramic_fullwidth | 9:16 | cropped/contain_partial | [93, 0, 228, 240] | 0.5625 | 0.0000% | 否(部分) | PASS |
| panoramic_fullwidth | 4:1_extreme | cropped/contain_partial | [0, 60, 320, 140] | 4.0000 | 0.0000% | 否(部分) | PASS |
| panoramic_fullwidth | 1:4_extreme | cropped/contain_partial | [130, 0, 190, 240] | 0.2500 | 0.0000% | 否(部分) | PASS |
| panoramic_fullwidth | 4:1 | rejected_subject_too_large | 拒绝 | - | - | - | - |
| flat_none | 1:1 | cropped/center_fallback | [40, 0, 280, 240] | 1.0000 | 0.0000% | 是 | PASS |
| flat_none | 16:9 | cropped/center_fallback | [0, 30, 320, 210] | 1.7778 | 0.0000% | 是 | PASS |
| flat_none | 9:16 | cropped/center_fallback | [92, 0, 227, 240] | 0.5625 | 0.0000% | 是 | PASS |
| flat_none | 4:1_extreme | cropped/center_fallback | [0, 80, 320, 160] | 4.0000 | 0.0000% | 是 | PASS |
| flat_none | 1:4_extreme | cropped/center_fallback | [130, 0, 190, 240] | 0.2500 | 0.0000% | 是 | PASS |
| noise_none | 1:1 | cropped/center_fallback | [40, 0, 280, 240] | 1.0000 | 0.0000% | 是 | PASS |
| noise_none | 16:9 | cropped/center_fallback | [0, 30, 320, 210] | 1.7778 | 0.0000% | 是 | PASS |
| noise_none | 9:16 | cropped/center_fallback | [92, 0, 227, 240] | 0.5625 | 0.0000% | 是 | PASS |
| noise_none | 4:1_extreme | cropped/center_fallback | [0, 80, 320, 160] | 4.0000 | 0.0000% | 是 | PASS |
| noise_none | 1:4_extreme | cropped/center_fallback | [130, 0, 190, 240] | 0.2500 | 0.0000% | 是 | PASS |
| faint_subject | 1:1 | cropped/center_fallback | [40, 0, 280, 240] | 1.0000 | 0.0000% | 是 | PASS |
| faint_subject | 16:9 | cropped/center_fallback | [0, 30, 320, 210] | 1.7778 | 0.0000% | 是 | PASS |
| faint_subject | 9:16 | cropped/center_fallback | [92, 0, 227, 240] | 0.5625 | 0.0000% | 是 | PASS |
| faint_subject | 4:1_extreme | cropped/center_fallback | [0, 80, 320, 160] | 4.0000 | 0.0000% | 是 | PASS |
| faint_subject | 1:4_extreme | cropped/center_fallback | [130, 0, 190, 240] | 0.2500 | 0.0000% | 是 | PASS |
| flat_none | reject_no_subject | rejected_no_subject | 拒绝 | - | - | - | - |

## 结论

全部断言通过：
- 居中/贴左/贴角/贴右/贯穿主体均被检出且 1:1 下完整保留；
- 纯色图与纯噪声图判定为无主体，居中退路生效；
- 所有裁剪框均落在图像范围内，比例误差 <= 1%；
- 极端比例下主体无法完整包含时 contain_partial 标记部分覆盖，reject 策略确定性拒绝。
