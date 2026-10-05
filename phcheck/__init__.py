"""phcheck：译文占位符校验与还原（仅标准库）。"""
from .tokens import CONVERSIONS, Chunk, InvalidItem, ScanResult, Token, scan
from .validate import Diff, FormatDrift, Report, validate
from .render import RenderError, restore

__version__ = "1.0.0"
__all__ = [
    "CONVERSIONS",
    "Chunk",
    "InvalidItem",
    "ScanResult",
    "Token",
    "scan",
    "Diff",
    "FormatDrift",
    "Report",
    "validate",
    "RenderError",
    "restore",
]
