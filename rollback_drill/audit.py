"""Append-only JSONL audit log shared by drill and real runs."""

import json
import os
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class AuditEvent:
    ts: float
    run_id: str
    mode: str
    step_id: str
    event: str
    detail: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "run_id": self.run_id,
            "mode": self.mode,
            "step_id": self.step_id,
            "event": self.event,
            "detail": self.detail,
        }


class JsonlAuditSink:
    def __init__(self, path: Optional[str]):
        self.path = path
        if path:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def emit(self, event: AuditEvent) -> None:
        if not self.path:
            return
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
