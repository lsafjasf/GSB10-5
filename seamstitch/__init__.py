"""Stdlib-only image stitching: exposure compensation + DP seam + blending."""

from .image import Image
from .stitch import StitchResult, stitch

__all__ = ["Image", "StitchResult", "stitch"]
