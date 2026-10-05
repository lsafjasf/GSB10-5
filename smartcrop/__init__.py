"""smartcrop: subject-aware auto-cropping with deterministic fallback.

Pure Python 3 standard library. No third-party dependencies.
"""
from .analysis import analyze, SubjectResult, SubjectMetrics
from .cropper import auto_crop, CropResult
from .image import Image, load_image, save_ppm, crop_image

__version__ = "1.0.0"
__all__ = [
    "analyze", "SubjectResult", "SubjectMetrics",
    "auto_crop", "CropResult",
    "Image", "load_image", "save_ppm", "crop_image",
]
