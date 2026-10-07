"""Persistent idempotency journal.

Every finished compensation unit is recorded by its key ``task_id@fire_at``.
The journal is the single source of truth for "already compensated":

* re-running the same plan skips keys already present (idempotent replay);
* after a crash / second downtime the journal is reloaded and the resume
  planner owes exactly the keys not yet recorded.

Storage is an append-only text file, one key per line.
"""
from __future__ import annotations

import os
import threading
from typing import Iterator, Set


class Journal:
    def __init__(self, path: str):
        self.path = path
        self._done: Set[str] = set()
        self._lock = threading.Lock()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    key = line.strip()
                    if key:
                        self._done.add(key)

    def __contains__(self, key: str) -> bool:
        return key in self._done

    def __len__(self) -> int:
        return len(self._done)

    def keys(self) -> Set[str]:
        return set(self._done)

    def is_done(self, key: str) -> bool:
        return key in self._done

    def mark_done(self, key: str, fsync: bool = False) -> bool:
        """Record ``key`` as compensated. Returns False if it was already done."""
        with self._lock:
            if key in self._done:
                return False
            self._done.add(key)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(key + "\n")
                fh.flush()
                if fsync:
                    os.fsync(fh.fileno())
            return True

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._done))
