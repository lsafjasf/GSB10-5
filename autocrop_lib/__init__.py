"""图像自动裁剪与主体定位库（纯标准库实现）。

子模块:
- ppm:       无依赖的 PGM(P5)/PPM(P6) 读写
- saliency:  边缘密度 + 局部对比度 + 空间凝聚度的主体判据
- cropper:   目标比例裁剪框求解、贴边保护、居中/拒绝退路
- autocrop:  对外整合接口
"""

from .ppm import Image, load, save
from .saliency import analyze_subject, CriterionConfig
from .cropper import crop, FitPolicy, CropResult
from .autocrop import autocrop, AutocropConfig

__all__ = [
    "Image",
    "load",
    "save",
    "analyze_subject",
    "CriterionConfig",
    "crop",
    "FitPolicy",
    "CropResult",
    "autocrop",
    "AutocropConfig",
]
