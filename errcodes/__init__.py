"""统一错误码库：规则解析、冲突检测、翻译、发布稳定性校验。"""

from .core import (
    AREA_MAP,
    CATEGORY_NAMES,
    category_name,
    is_valid_code,
    make_code,
    parse_code,
)
from .registry import (
    SEVERITY_ERROR,
    SEVERITY_WARN,
    build,
    build_from_paths,
    load_modules,
)
from .stability import (
    check_stability,
    load_snapshot,
    save_snapshot,
    snapshot_from_registry,
)
from .translate import (
    FALLBACK_CODE,
    FALLBACK_DEFAULT,
    Translator,
    load_locale,
)

__all__ = [
    "AREA_MAP", "CATEGORY_NAMES", "category_name", "is_valid_code",
    "make_code", "parse_code",
    "build", "build_from_paths", "load_modules",
    "SEVERITY_ERROR", "SEVERITY_WARN",
    "Translator", "load_locale", "FALLBACK_CODE", "FALLBACK_DEFAULT",
    "check_stability", "load_snapshot", "save_snapshot",
    "snapshot_from_registry",
]
