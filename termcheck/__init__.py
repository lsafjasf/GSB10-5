"""termcheck：译名一致性检查库（仅标准库）。"""

from .checker import Checker, Report, check_files, iter_segments
from .termbase import Termbase, load, load_file

__version__ = "0.1.0"
__all__ = ["Checker", "Report", "Termbase", "load", "load_file",
           "check_files", "iter_segments"]
