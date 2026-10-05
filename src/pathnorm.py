"""pathnorm: cross-platform path normalization and equivalence (stdlib only).

Two path styles are supported, independent of the host OS:
  - "windows": both '/' and '\\' are separators; output uses '\\';
               UNC prefixes (\\\\server\\share) are preserved;
               reserved device names are rejected; trailing dots/spaces
               are stripped per Win32 rules.
  - "posix":   only '/' is a separator ('\\' is an ordinary character);
               output uses '/'.

Case sensitivity is a property of the *filesystem*, not the OS, so it is
an explicit parameter (case_sensitive=True/False), never guessed.

Deterministic failure policy: over-long paths and reserved names raise
typed errors; nothing is ever silently truncated.
"""

from __future__ import annotations

import re

__all__ = [
    "normalize",
    "equivalent",
    "check",
    "PathNormError",
    "ReservedNameError",
    "PathTooLongError",
    "ComponentTooLongError",
    "WINDOWS_MAX_PATH",
    "POSIX_MAX_PATH",
    "NAME_MAX",
    "RESERVED_NAMES",
]

# ---- Limits (deterministic, documented) -------------------------------------
WINDOWS_MAX_PATH = 260          # Win32 MAX_PATH (without \\?\ prefix)
WINDOWS_MAX_PATH_EXTENDED = 32767  # with \\?\ prefix
POSIX_MAX_PATH = 4096           # Linux PATH_MAX
NAME_MAX = 255                  # per-component limit (ext4/NTFS/...)

# ---- Windows reserved device names ------------------------------------------
RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)
_RESERVED_RE = re.compile(
    r"^(?:" + "|".join(sorted(RESERVED_NAMES)) + r")(?:\..*)?$", re.IGNORECASE
)


class PathNormError(ValueError):
    """Base class for pathnorm errors."""


class ReservedNameError(PathNormError):
    """A path component is a Windows reserved device name."""

    def __init__(self, component: str, path: str):
        self.component = component
        self.path = path
        super().__init__(
            f"reserved device name {component!r} in path {path!r} "
            f"(reserved: {sorted(RESERVED_NAMES)})"
        )


class PathTooLongError(PathNormError):
    """Full path exceeds the style's limit."""

    def __init__(self, path: str, length: int, limit: int):
        self.path, self.length, self.limit = path, length, limit
        super().__init__(
            f"path length {length} exceeds limit {limit}: {path[:64]!r}..."
        )


class ComponentTooLongError(PathNormError):
    """A single component exceeds NAME_MAX."""

    def __init__(self, component: str, length: int, limit: int = NAME_MAX):
        self.component, self.length, self.limit = component, length, limit
        super().__init__(
            f"component length {length} exceeds NAME_MAX {limit}: "
            f"{component[:64]!r}..."
        )


def _is_reserved(component: str) -> bool:
    return bool(_RESERVED_RE.match(component))


def normalize(
    path: str,
    style: str = "posix",
    *,
    case_sensitive: bool = True,
    strict: bool = True,
) -> str:
    """Normalize `path` to a canonical string for the given style.

    Rules (see RULES.md for the full table):
      R1 separators   : unify to the style separator; collapse duplicates.
      R2 dot segments : resolve '.' and '..' lexically (no FS access).
                        '..' at the root of an absolute path is dropped;
                        leading '..' of a relative path is preserved.
      R3 case         : case_sensitive=False -> Unicode casefold.
      R4 reserved     : windows style rejects CON/PRN/AUX/NUL/COM1-9/LPT1-9
                        (any case, any extension) via ReservedNameError.
      R5 trailing     : windows style strips trailing dots/spaces of each
                        component (Win32 behaviour).
      R6 length       : enforce MAX_PATH / PATH_MAX and NAME_MAX via
                        PathTooLongError / ComponentTooLongError.
      R7 UNC          : windows '\\\\server\\share' prefix preserved.

    strict=False downgrades R4/R6 errors to no-ops (still never truncates).
    """
    if not isinstance(path, str):
        raise TypeError(f"path must be str, got {type(path).__name__}")
    if style not in ("windows", "posix"):
        raise ValueError(f"style must be 'windows' or 'posix', got {style!r}")
    if "\x00" in path:
        raise PathNormError("NUL byte in path")

    win = style == "windows"
    sep = "\\" if win else "/"

    extended = False
    unc = False
    unc_ext = False
    prefix = ""
    rest = path

    if win:
        # R7: detect \\?\ and UNC prefixes before separator unification.
        unified = path.replace("/", "\\")
        rest = unified
        if unified.startswith("\\\\?\\"):
            extended = True
            prefix = "\\\\?\\"
            rest = unified[4:]
            if rest.startswith("UNC\\"):
                prefix += "UNC\\"
                rest = rest[4:]
                unc = True
                unc_ext = True
        if rest.startswith("\\\\"):
            unc = True
        rest = rest.replace("\\", "/")  # work in '/' internally
    else:
        rest = path  # posix: '\\' is an ordinary character, not a separator

    # Split into components; track absoluteness / UNC anchor.
    parts = rest.split("/")
    anchor = ""
    idx = 0
    if win and unc:
        # //server/share/... -> anchor \\server\share
        while idx < len(parts) and parts[idx] == "":
            idx += 1
        if idx + 1 < len(parts) or idx < len(parts):
            server = parts[idx] if idx < len(parts) else ""
            share = parts[idx + 1] if idx + 1 < len(parts) else ""
            lead = "" if unc_ext else "\\\\"
            anchor = lead + server + ("\\" + share if share else "")
            idx += 2 if share else 1
        absolute = True
    elif parts and parts[0] == "":
        absolute = True
        idx = 1
        if win:
            # drive-relative like "\foo" (no drive) keeps root anchor
            anchor = ""
    else:
        absolute = False

    drive = ""
    if win and idx < len(parts) and re.match(r"^[A-Za-z]:$", parts[idx]):
        drive = parts[idx].upper()  # drive letters canonicalized upper-case
        idx += 1
        if absolute:
            anchor = drive + "\\"
        else:
            anchor = drive  # drive-relative: C:foo

    # R2: lexical dot-segment resolution.
    out: list[str] = []
    for comp in parts[idx:]:
        if comp in ("", "."):
            continue  # R1: duplicate separators collapse here too
        if comp == "..":
            if out and out[-1] != "..":
                out.pop()
            elif not absolute:
                out.append("..")
            # '..' at absolute root: dropped
            continue
        out.append(comp)

    # R5: windows strips trailing dots/spaces per component.
    if win:
        out = [c.rstrip(" .") if c not in ("..",) else c for c in out]

    # R4: reserved device names (windows).
    if win and strict:
        for comp in out:
            if _is_reserved(comp):
                raise ReservedNameError(comp, path)

    # R6: component length.
    if strict:
        for comp in out:
            if len(comp) > NAME_MAX:
                raise ComponentTooLongError(comp, len(comp))

    # R3: case folding for case-insensitive filesystems.
    if not case_sensitive:
        out = [c.casefold() for c in out]
        if win:
            anchor = anchor.casefold() if not anchor.startswith("\\\\?\\") else anchor
        prefix = prefix  # prefix is already canonical

    # Reassemble.
    if win:
        body = "\\".join(out)
        if anchor.endswith("\\"):
            result = anchor + body
        elif anchor:
            result = anchor + ("\\" + body if body else "")
        else:
            result = body
        if not result and absolute:
            result = "\\"
        result = prefix + result
        limit = WINDOWS_MAX_PATH_EXTENDED if extended else WINDOWS_MAX_PATH
    else:
        body = "/".join(out)
        result = ("/" + body) if absolute else body
        if not result and absolute:
            result = "/"
        limit = POSIX_MAX_PATH

    # R6: full path length.
    if strict and len(result) > limit:
        raise PathTooLongError(result, len(result), limit)

    return result


def equivalent(
    a: str,
    b: str,
    style: str = "posix",
    *,
    case_sensitive: bool = True,
) -> bool:
    """True iff `a` and `b` denote the same location under the given rules."""
    return (
        normalize(a, style, case_sensitive=case_sensitive)
        == normalize(b, style, case_sensitive=case_sensitive)
    )


def check(path: str, style: str = "posix") -> dict:
    """Non-raising report for one path (used by tooling/CLI)."""
    report = {"input": path, "style": style, "ok": True, "error": None}
    try:
        report["normalized_cs"] = normalize(path, style, case_sensitive=True)
        report["normalized_ci"] = normalize(path, style, case_sensitive=False)
    except PathNormError as exc:
        report["ok"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
    return report
