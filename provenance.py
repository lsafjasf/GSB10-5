"""Artifact provenance & traceability library (Python 3, stdlib only).

Core ideas
----------
* Every build emits one JSON **record** capturing: artifact digest, source
  commit + source-tree digest, build parameters, toolchain, dependencies.
* The record is serialized as **canonical JSON** (sorted keys, tight
  separators) and hashed with SHA-256 -> ``record_sha256``.  That digest is
  also the ``build_id``: it makes the record tamper-evident and makes two
  builds of the same source distinguishable (timestamp + nonce differ).
* Records are appended to a JSONL **ledger**.  An in-memory **Index** maps
  artifact-id / build-id / commit -> file offsets, so reverse lookups are
  O(1) with a tiny memory footprint even for 100k+ artifacts.

Reverse lookup chain (complete, no broken links):
    artifact sha256  ->  provenance record (verified)  ->  source commit
                     ->  build params / toolchain / dependencies
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import uuid
from datetime import datetime, timezone

SCHEMA = "gsb-provenance/1"
_CHUNK = 1 << 20

REQUIRED_FIELDS = ("schema", "artifact", "source", "build", "toolchain",
                   "dependencies", "record_sha256", "build_id")


# ---------------------------------------------------------------- hashing --

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_tree(root: str) -> str:
    """Deterministic digest of a directory tree (paths + file contents)."""
    h = hashlib.sha256()
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            entries.append((rel, full))
    entries.sort()
    for rel, full in entries:
        h.update(rel.replace(os.sep, "/").encode("utf-8"))
        h.update(b"\0")
        h.update(hash_file(full).encode("ascii"))
        h.update(b"\0")
    return h.hexdigest()


# ------------------------------------------------------------- canonical --

def canonical_json(obj) -> bytes:
    """Deterministic serialization: sorted keys, tight separators."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def record_digest(record: dict) -> str:
    """SHA-256 over the canonical record, excluding integrity fields."""
    body = {k: v for k, v in record.items()
            if k not in ("record_sha256", "build_id")}
    return sha256_bytes(canonical_json(body))


def finalize_record(record: dict) -> dict:
    """Compute and attach record_sha256 / build_id (in place + returned)."""
    record.pop("record_sha256", None)
    record.pop("build_id", None)
    digest = record_digest(record)
    record["record_sha256"] = digest
    record["build_id"] = digest  # build_id IS the integrity digest
    return record


# ------------------------------------------------------------ record build --

def detect_toolchain() -> dict:
    return {
        "python": platform.python_version(),
        "os": platform.system(),
        "os_release": platform.release(),
        "platform": platform.platform(),
        "machine": platform.machine(),
    }


def create_record(*, artifact_path: str, name: str, source: dict,
                  build: dict | None = None, toolchain: dict | None = None,
                  dependencies: dict | None = None) -> dict:
    """Create a finalized provenance record for a built artifact.

    source:       {"vcs","repo","commit","tree_sha256","dirty"}
    build:        extra build params (command/args/flags); timestamp+nonce
                  are added here so identical inputs still yield unique ids.
    dependencies: {"lockfile_sha256": str, "resolved": [{name,version,digest}]}
    """
    record = {
        "schema": SCHEMA,
        "artifact": {
            "name": name,
            "sha256": hash_file(artifact_path),
            "size": os.path.getsize(artifact_path),
        },
        "source": source,
        "build": dict(build or {}),
        "toolchain": toolchain if toolchain is not None else detect_toolchain(),
        "dependencies": dependencies or {"lockfile_sha256": None, "resolved": []},
    }
    record["build"].setdefault("builder", os.environ.get("USER", "unknown"))
    record["build"]["finished_at"] = datetime.now(timezone.utc).isoformat()
    record["build"]["nonce"] = uuid.uuid4().hex
    return finalize_record(record)


# --------------------------------------------------------------- verify --

def verify_record(record: dict) -> list[str]:
    """Return list of integrity problems; empty list means OK."""
    problems = []
    for field in REQUIRED_FIELDS:
        if field not in record:
            problems.append(f"missing field: {field}")
    if problems:
        return problems
    if record["schema"] != SCHEMA:
        problems.append(f"unknown schema: {record['schema']}")
    expect = record_digest(record)
    if record["record_sha256"] != expect:
        problems.append(
            f"record_sha256 mismatch: stored={record['record_sha256']} "
            f"recomputed={expect}")
    if record["build_id"] != record["record_sha256"]:
        problems.append(
            f"build_id/record_sha256 inconsistent: {record['build_id']} != "
            f"{record['record_sha256']}")
    art = record.get("artifact", {})
    if not isinstance(art.get("sha256"), str) or len(art["sha256"]) != 64:
        problems.append("artifact.sha256 malformed")
    return problems


def verify_artifact_file(record: dict, artifact_path: str) -> list[str]:
    """Check the artifact file itself against the record."""
    problems = verify_record(record)
    if not os.path.exists(artifact_path):
        return problems + [f"artifact file missing: {artifact_path}"]
    actual = hash_file(artifact_path)
    if actual != record["artifact"]["sha256"]:
        problems.append(
            f"artifact digest mismatch: recorded={record['artifact']['sha256']} "
            f"actual={actual}")
    actual_size = os.path.getsize(artifact_path)
    if actual_size != record["artifact"]["size"]:
        problems.append(
            f"artifact size mismatch: recorded={record['artifact']['size']} "
            f"actual={actual_size}")
    return problems


# ---------------------------------------------------------------- ledger --

class Ledger:
    """Append-only JSONL store of provenance records."""

    def __init__(self, path: str):
        self.path = path

    def append(self, record: dict) -> None:
        if verify_record(record):
            raise ValueError("refusing to append invalid record")
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(canonical_json(record).decode("ascii") + "\n")

    def records(self):
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def verify_all(self):
        """Verify every record. Returns (ok_count, [(line_no, problems)])."""
        ok, bad = 0, []
        with open(self.path, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    bad.append((lineno, [f"invalid JSON: {exc}"]))
                    continue
                problems = verify_record(record)
                if problems:
                    bad.append((lineno, problems))
                else:
                    ok += 1
        return ok, bad


# ----------------------------------------------------------------- index --

class Index:
    """Offset index over a ledger file: O(1) reverse lookups, small memory.

    Maps identifier -> file offset(s); the record itself is read from disk
    on demand, so 100k artifacts cost only a few MB of index memory.
    """

    def __init__(self, ledger_path: str):
        self.ledger_path = ledger_path
        self.by_artifact = {}   # artifact sha256 -> [offset]
        self.by_build = {}      # build_id -> offset
        self.by_commit = {}     # source commit -> [offset]
        self.count = 0

    @classmethod
    def build(cls, ledger_path: str) -> "Index":
        idx = cls(ledger_path)
        with open(ledger_path, "rb") as fh:
            while True:
                offset = fh.tell()
                line = fh.readline()
                if not line:
                    break
                if not line.strip():
                    continue
                record = json.loads(line)
                art = record["artifact"]["sha256"]
                idx.by_artifact.setdefault(art, []).append(offset)
                idx.by_build[record["build_id"]] = offset
                idx.by_commit.setdefault(record["source"]["commit"], []).append(offset)
                idx.count += 1
        return idx

    def _load(self, offset: int) -> dict:
        with open(self.ledger_path, "rb") as fh:
            fh.seek(offset)
            return json.loads(fh.readline())

    def lookup_artifact(self, artifact_sha256: str) -> list[dict]:
        return [self._load(o) for o in self.by_artifact.get(artifact_sha256, [])]

    def lookup_build(self, build_id: str) -> dict | None:
        offset = self.by_build.get(build_id)
        return self._load(offset) if offset is not None else None

    def lookup_commit(self, commit: str) -> list[dict]:
        return [self._load(o) for o in self.by_commit.get(commit, [])]

    def trace(self, artifact_sha256: str) -> list[dict]:
        """Full reverse chain: artifact id -> verified record -> commit."""
        chain = []
        for record in self.lookup_artifact(artifact_sha256):
            problems = verify_record(record)
            chain.append({
                "artifact_sha256": artifact_sha256,
                "build_id": record["build_id"],
                "integrity_ok": not problems,
                "integrity_problems": problems,
                "source_commit": record["source"]["commit"],
                "source_tree_sha256": record["source"].get("tree_sha256"),
                "source_repo": record["source"].get("repo"),
                "build": record["build"],
                "toolchain": record["toolchain"],
                "dependencies": record["dependencies"],
            })
        return chain

    def memory_bytes(self) -> int:
        return (_deep_size(self.by_artifact) + _deep_size(self.by_build)
                + _deep_size(self.by_commit))


def _deep_size(obj, _seen=None) -> int:
    seen = _seen if _seen is not None else set()
    oid = id(obj)
    if oid in seen:
        return 0
    seen.add(oid)
    size = sys.getsizeof(obj)
    if isinstance(obj, dict):
        size += sum(_deep_size(k, seen) + _deep_size(v, seen)
                    for k, v in obj.items())
    elif isinstance(obj, (list, tuple, set, frozenset)):
        size += sum(_deep_size(i, seen) for i in obj)
    return size


# ------------------------------------------------------------------- cli --

def _cli(argv=None) -> int:
    parser = argparse.ArgumentParser(description="artifact provenance tool")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_rec = sub.add_parser("record", help="record a build")
    p_rec.add_argument("--ledger", required=True)
    p_rec.add_argument("--artifact", required=True)
    p_rec.add_argument("--name", required=True)
    p_rec.add_argument("--commit", required=True)
    p_rec.add_argument("--repo", default="")
    p_rec.add_argument("--src-dir", default=None,
                       help="source tree to digest (tree_sha256)")
    p_rec.add_argument("--param", action="append", default=[],
                       help="build param k=v (repeatable)")

    p_lu = sub.add_parser("lookup", help="artifact id -> full chain")
    p_lu.add_argument("--ledger", required=True)
    p_lu.add_argument("--artifact-id", required=True)

    p_vf = sub.add_parser("verify", help="verify ledger integrity")
    p_vf.add_argument("--ledger", required=True)

    args = parser.parse_args(argv)

    if args.cmd == "record":
        params = dict(kv.split("=", 1) for kv in args.param)
        source = {
            "vcs": "git", "repo": args.repo, "commit": args.commit,
            "tree_sha256": hash_tree(args.src_dir) if args.src_dir else None,
            "dirty": False,
        }
        record = create_record(artifact_path=args.artifact, name=args.name,
                               source=source, build={"params": params})
        Ledger(args.ledger).append(record)
        print(json.dumps({"build_id": record["build_id"],
                          "artifact_sha256": record["artifact"]["sha256"]},
                         indent=2))
        return 0

    if args.cmd == "lookup":
        idx = Index.build(args.ledger)
        print(json.dumps(idx.trace(args.artifact_id), indent=2))
        return 0

    if args.cmd == "verify":
        ok, bad = Ledger(args.ledger).verify_all()
        print(json.dumps({"ok": ok, "bad": [
            {"line": n, "problems": p} for n, p in bad]}, indent=2))
        return 1 if bad else 0

    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
