"""Idempotent side-effect store used by the runner and the tests.

Real handlers must apply effects keyed by the compensation key
(``task_id@fire_at``) with an upsert/INSERT-OR-IGNORE so that a retried
execution is a no-op. ``EffectLog`` models exactly that contract and counts
how many times each key was *applied*, which is what makes duplicate-side-
effect violations observable in tests.
"""
from __future__ import annotations

import threading
from collections import Counter
from typing import Dict, List, Tuple


class EffectLog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: Dict[str, Tuple[str, int]] = {}
        self.apply_calls = 0
        self.duplicate_applies = 0
        self._per_key_calls: Counter = Counter()

    def apply(self, key: str, task_id: str, fire_at: int) -> None:
        """Idempotent upsert of the effect for ``key``."""
        with self._lock:
            self.apply_calls += 1
            self._per_key_calls[key] += 1
            if key in self._records:
                self.duplicate_applies += 1
                return
            self._records[key] = (task_id, int(fire_at))

    def __len__(self) -> int:
        return len(self._records)

    def __contains__(self, key: str) -> bool:
        return key in self._records

    def keys(self) -> List[str]:
        return sorted(self._records)

    def calls_for(self, key: str) -> int:
        return self._per_key_calls.get(key, 0)

    def duplicates(self) -> Dict[str, int]:
        """Keys applied more than once -> number of applications."""
        return {k: c for k, c in self._per_key_calls.items() if c > 1}
