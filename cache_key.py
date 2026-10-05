"""Build cache key design (stdlib only).

A build cache key must capture EVERY input that can affect the build
output.  If even one factor is missing, an incompatible artifact can be
reused ("false hit" / false-positive cache hit) and the failure only
surfaces at runtime.

Factors included in the key (see FACTORS.md for the rationale):

  1. source     - content hash of every source file, keyed by its path
                  RELATIVE to the source root (absolute path independent)
  2. toolchain  - compiler/interpreter name + version (+ driver hashes)
  3. params     - build parameters/flags in their original order
  4. env        - the build-affecting environment variables (allow-list:
                  the caller passes exactly the ones that may change output)
  5. platform   - OS, architecture, libc/runtime version

The key is sha256 over a canonical JSON encoding of all five factors.
Canonical JSON (sorted keys, fixed separators, UTF-8) removes any
representation-level ambiguity, so the same logical input always yields
the same bytes and therefore the same key.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

KEY_VERSION = 1


def canonical_bytes(payload: object) -> bytes:
    """Deterministic byte encoding shared by every part of the key."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def digest_payload(payload: object) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


# ---------------------------------------------------------------------------
# Factor 1: source
# ---------------------------------------------------------------------------

DEFAULT_IGNORE_DIRS = frozenset({".git", "__pycache__", "node_modules", "build", "dist"})


def source_manifest(
    root: str,
    ignore_dirs: Iterable[str] = DEFAULT_IGNORE_DIRS,
) -> List[Dict[str, object]]:
    """Build a manifest of source files.

    Each entry is identified by its POSIX-style path RELATIVE to `root`.
    Only content (sha256) and size are recorded - never mtime, inode or
    the absolute path, so moving the checkout to another directory does
    not change the manifest.
    """
    ignore = frozenset(ignore_dirs)
    entries: List[Dict[str, object]] = []
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in ignore)
        for name in sorted(filenames):
            abspath = os.path.join(dirpath, name)
            relpath = os.path.relpath(abspath, root).replace(os.sep, "/")
            h = hashlib.sha256()
            size = 0
            with open(abspath, "rb") as fh:
                while True:
                    chunk = fh.read(1 << 20)
                    if not chunk:
                        break
                    size += len(chunk)
                    h.update(chunk)
            entries.append({"path": relpath, "size": size, "sha256": h.hexdigest()})
    entries.sort(key=lambda e: e["path"])
    return entries


# ---------------------------------------------------------------------------
# Factors 2-5 helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Toolchain:
    name: str
    version: str
    # Optional opaque extras that can affect output, e.g. {"target":
    # "x86_64-linux-gnu", "driver_sha256": "..."}.
    extras: Mapping[str, str] = field(default_factory=dict)

    def to_json(self) -> Dict[str, object]:
        return {"name": self.name, "version": self.version, "extras": dict(self.extras)}


@dataclass(frozen=True)
class PlatformInfo:
    os_name: str
    arch: str
    libc: str  # e.g. "glibc-2.38" or "musl-1.2.4"

    def to_json(self) -> Dict[str, object]:
        return {"os": self.os_name, "arch": self.arch, "libc": self.libc}


def selected_env(os_env: Mapping[str, str], names: Sequence[str]) -> Dict[str, str]:
    """Pick the build-affecting variables from the process environment.

    An allow-list (not a deny-list) is used on purpose: a new variable
    that starts influencing the build cannot silently get into the key,
    and PATH-like absolute-path-heavy variables are excluded unless the
    caller proves they matter (absolute paths would break path
    independence).  Missing variables are recorded as the empty string,
    i.e. "unset" and "set to empty" are treated identically; callers
    that need to distinguish them can wrap the value.
    """
    return {name: os_env.get(name, "") for name in names}


# ---------------------------------------------------------------------------
# Key computation
# ---------------------------------------------------------------------------

def build_key_payload(
    manifest: Sequence[Mapping[str, object]],
    toolchain: Toolchain,
    params: Sequence[str],
    env: Mapping[str, str],
    platform: PlatformInfo,
) -> Dict[str, object]:
    """Assemble the complete, JSON-serialisable key input."""
    return {
        "v": KEY_VERSION,
        "source": list(manifest),
        "toolchain": toolchain.to_json(),
        # params keep their order: flag order can be semantically
        # significant (e.g. linker -L/-l resolution), so do NOT sort.
        "params": list(params),
        "env": dict(env),
        "platform": platform.to_json(),
    }


def compute_key(
    manifest: Sequence[Mapping[str, object]],
    toolchain: Toolchain,
    params: Sequence[str],
    env: Mapping[str, str],
    platform: PlatformInfo,
) -> str:
    """Return the 64-char hex sha256 cache key."""
    return digest_payload(
        build_key_payload(manifest, toolchain, params, env, platform)
    )


def lookup(cache: Mapping[str, str], key: str) -> Optional[str]:
    """Cache lookup: a hit is ONLY possible on an exact key match."""
    return cache.get(key)
