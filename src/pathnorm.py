"""pathnorm — 跨平台路径规范化与等价性判定（仅标准库）。

设计目标：同一份配置在 Windows / POSIX 上写出略有差异的路径时，
能够确定性地判断两者是否指向同一位置，并对超长路径、保留名给出
确定性报错（绝不静默截断）。

规范化规则概要（详见 RULES.md 规则表）：
  R1 分隔符    : '\\' 与 '/' 都视为分隔符，统一为 '/'
  R2 重复分隔符: 折叠为单个（UNC 前缀 //server/share 除外）
  R3 相对分量  : 去除 '.'；'..' 按词法抵消前一个普通分量；
                 相对路径开头无法抵消的 '..' 原样保留
  R4 末尾分隔符: 去除（根 '/'、'C:/' 除外）
  R5 盘符      : 'c:' 规范为 'C:'
  R6 大小写    : case_sensitive=False 时比较键 casefold，展示形式保留原样
  R7 保留名    : Windows 设备名（CON/PRN/AUX/NUL/COM1-9/LPT1-9，
                 不区分大小写、含扩展名如 con.txt）在 windows 语义下报错
  R8 超长路径  : windows 260（\\\\?\\ 前缀时 32767）、posix 4096，
                 超限抛 PathTooLongError，绝不截断
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

__all__ = [
    "PathNormError",
    "PathTooLongError",
    "ReservedNameError",
    "EmptyPathError",
    "NormalizedPath",
    "normalize",
    "equivalent",
    "validate",
    "WINDOWS_MAX_PATH",
    "WINDOWS_MAX_PATH_EXTENDED",
    "POSIX_PATH_MAX",
    "WINDOWS_RESERVED_NAMES",
]

WINDOWS_MAX_PATH = 260            # 不含结尾 NUL 的传统 MAX_PATH
WINDOWS_MAX_PATH_EXTENDED = 32767  # \\?\ 前缀后的上限
POSIX_PATH_MAX = 4096

# Windows 保留设备名（不含扩展名部分）
WINDOWS_RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)


class PathNormError(ValueError):
    """路径规范化相关错误的基类。"""


class EmptyPathError(PathNormError):
    """空路径。"""


class PathTooLongError(PathNormError):
    """路径超过目标语义的长度上限。绝不静默截断。"""

    def __init__(self, path: str, limit: int, style: str):
        self.path = path
        self.limit = limit
        self.style = style
        super().__init__(
            f"路径超长: 长度 {len(path)} 超过 {style} 上限 {limit}: {path[:80]!r}..."
        )


class ReservedNameError(PathNormError):
    """路径分量命中 Windows 保留设备名。"""

    def __init__(self, component: str, path: str):
        self.component = component
        self.path = path
        super().__init__(f"保留设备名 {component!r} 出现在路径中: {path!r}")


def _is_reserved(component: str) -> bool:
    """判断单个路径分量是否为 Windows 保留名。

    规则：取分量最后一个 '.' 之前的主名（无 '.' 则整名），
    大写后比对保留名表。'con.txt'、'CON'、'com1' 均命中。
    """
    stem = component.rsplit(".", 1)[0] if "." in component else component
    return stem.upper() in WINDOWS_RESERVED_NAMES


@dataclass(frozen=True)
class NormalizedPath:
    """规范化结果。

    display : 保留原始大小写的规范形式（用于展示/写回）
    key     : 等价性比较键（按 case_sensitive 决定是否 casefold）
    style   : 'windows' | 'posix'
    is_absolute / drive / components : 结构信息
    """

    display: str
    key: str
    style: str
    is_absolute: bool
    drive: str
    components: tuple


def _split_anchor(path: str, style: str):
    """拆出锚点（drive / UNC / 根），返回 (drive, is_absolute, rest)。"""
    drive = ""
    is_absolute = False
    rest = path

    if style == "windows":
        # 扩展长度前缀 \\?\ 或 //?/
        if rest[:4] in ("\\\\?\\", "//?/"):
            rest = rest[4:]
        # 盘符
        if len(rest) >= 2 and rest[1] == ":" and rest[0].isalpha():
            drive = rest[0].upper() + ":"
            rest = rest[2:]
        # UNC //server/share
        elif rest.startswith("//"):
            parts = rest[2:].split("/")
            if len(parts) >= 2 and parts[0] and parts[1]:
                drive = "//" + parts[0] + "/" + parts[1]
                rest = "/".join(parts[2:])
                is_absolute = True
                return drive, is_absolute, rest
        if rest.startswith("/"):
            is_absolute = True
            rest = rest.lstrip("/")
    else:  # posix
        if rest.startswith("/"):
            is_absolute = True
            rest = rest.lstrip("/")

    return drive, is_absolute, rest


def normalize(path: str, style: str = "windows", case_sensitive: bool = False,
              validate_limits: bool = True) -> NormalizedPath:
    """规范化路径。

    参数:
        path           : 原始路径字符串
        style          : 'windows' 或 'posix'，决定保留名/长度上限/盘符语义
        case_sensitive : 文件系统是否大小写敏感（影响比较键 key）
        validate_limits: 是否执行保留名与长度校验（默认开）

    返回 NormalizedPath。出错抛 PathNormError 子类，绝不静默截断。
    """
    if style not in ("windows", "posix"):
        raise ValueError(f"未知 style: {style!r}")
    if not isinstance(path, str) or path == "":
        raise EmptyPathError("路径为空")
    if "\x00" in path:
        raise PathNormError("路径包含 NUL 字符")

    # R1: 统一分隔符
    unified = path.replace("\\", "/")
    # R2: 折叠重复分隔符（保留开头以便锚点解析）
    while "//" in unified[1:]:
        unified = unified[0] + unified[1:].replace("//", "/")

    drive, is_absolute, rest = _split_anchor(unified, style)

    # R3: 词法解析 . 与 ..
    out: list[str] = []
    for comp in rest.split("/"):
        if comp in ("", "."):
            continue
        if comp == "..":
            if out and out[-1] != "..":
                out.pop()
            elif not is_absolute:
                out.append("..")
            # 绝对路径下越界的 '..' 直接丢弃（等价于停在根）
        else:
            out.append(comp)

    # R7: 保留名校验（仅 windows 语义）
    if validate_limits and style == "windows":
        for comp in out:
            if _is_reserved(comp):
                raise ReservedNameError(comp, path)

    # 组装展示形式
    body = "/".join(out)
    if style == "windows":
        if drive.startswith("//"):
            display = drive + ("/" + body if body else "")
        elif drive:
            display = drive + "/" + body if body else drive + "/"
        else:
            display = ("/" if is_absolute else "") + body
    else:
        display = ("/" if is_absolute else "") + body
    if display == "":
        display = "/" if is_absolute else "."

    # R8: 长度校验（按展示形式计）
    if validate_limits:
        if style == "windows":
            extended = path[:4] in ("\\\\?\\", "//?/")
            limit = WINDOWS_MAX_PATH_EXTENDED if extended else WINDOWS_MAX_PATH
        else:
            limit = POSIX_PATH_MAX
        if len(display) > limit:
            raise PathTooLongError(display, limit, style)

    # R6: 比较键
    key = display if case_sensitive else unicodedata.normalize("NFC", display).casefold()

    return NormalizedPath(
        display=display,
        key=key,
        style=style,
        is_absolute=is_absolute,
        drive=drive,
        components=tuple(out),
    )


def equivalent(a: str, b: str, style: str = "windows",
               case_sensitive: bool = False) -> bool:
    """判断两个写法不同的路径是否指向同一位置。"""
    na = normalize(a, style=style, case_sensitive=case_sensitive)
    nb = normalize(b, style=style, case_sensitive=case_sensitive)
    return na.key == nb.key


def validate(path: str, style: str = "windows") -> NormalizedPath:
    """仅做校验+规范化，等价于 normalize(..., case_sensitive=True)。"""
    return normalize(path, style=style, case_sensitive=True)
