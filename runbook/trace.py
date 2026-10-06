"""执行轨迹：JSONL 追加写，逐步可核对输入/输出。"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class Event:
    seq: int
    ts: str
    run_id: str
    book_id: str
    step_id: Optional[str]
    kind: str                    # run_start/step_input/step_output/wait_human/
                                 # human_decision/checkpoint/interrupt/resume/
                                 # takeover/abort/run_end ...
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "run_id": self.run_id,
            "book_id": self.book_id,
            "step_id": self.step_id,
            "kind": self.kind,
            "payload": self.payload,
        }


class TraceRecorder:
    """把每一步的输入/输出/人工决策落盘为 JSONL，支持事后核对。"""

    def __init__(self, path: str, run_id: str, book_id: str):
        self.path = path
        self.run_id = run_id
        self.book_id = book_id
        self._seq = 0
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._fh = open(path, "a", encoding="utf-8")

    def emit(self, event_name: str, step_id: Optional[str] = None,
             **payload: Any) -> Event:
        self._seq += 1
        ev = Event(
            seq=self._seq,
            ts=utc_now(),
            run_id=self.run_id,
            book_id=self.book_id,
            step_id=step_id,
            kind=event_name,
            payload=payload,
        )
        self._fh.write(json.dumps(ev.to_dict(), ensure_ascii=False) + "\n")
        self._fh.flush()
        return ev

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "TraceRecorder":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def read_trace(path: str) -> List[Dict[str, Any]]:
    """读取轨迹文件（校验用）。"""
    events: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    return events
